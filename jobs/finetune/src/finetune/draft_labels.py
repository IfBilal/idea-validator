"""Draft labels from a hosted model (P8-T02): ``python -m finetune.draft_labels``.

Runs locally with the team's key outside the cluster; the client sits behind ``ChatClient`` so it
can be pointed at M5 ``/llm/v1/chat/completions`` later. Temperature 0, JSON mode, at least 800
completion tokens (reasoning models spend them on thinking). The reply is held to the same
contract as a training target (``validate_example``): off-grid scores, invented evidence ids,
verdict wording and extra keys are all rejected, retried once, then the candidate is dropped.

Draft labels are NOT ground truth. They are only ever ``hand_checked: false`` until a human
review (``finetune.review``) says otherwise.
"""

import argparse
import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
import yaml

from .prompting import PromptFactory, make_example, repo_root
from .schema import ExampleError, validate_example

MAX_TOKENS = 800
ATTEMPTS = 2


@dataclass(frozen=True)
class ChatResult:
    text: str
    tokens_in: int
    tokens_out: int


class ChatClient(Protocol):
    model: str

    def complete(self, *, system: str, user: str, max_tokens: int) -> ChatResult: ...


class CostTracker:
    def __init__(self, *, price_in_per_mtok: float, price_out_per_mtok: float) -> None:
        self._pin = price_in_per_mtok
        self._pout = price_out_per_mtok
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls = 0

    def add(self, tokens_in: int, tokens_out: int) -> None:
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.calls += 1

    @property
    def cost_usd(self) -> float:
        return (self.tokens_in * self._pin + self.tokens_out * self._pout) / 1_000_000


def load_prices(config_path: Path, alias: str) -> dict[str, Any]:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    entry = data["aliases"][alias]
    return {
        "model": entry["model"],
        "base_url": entry["base_url"],
        "price_in_per_mtok": float(entry["price_in_per_mtok"]),
        "price_out_per_mtok": float(entry["price_out_per_mtok"]),
    }


class HostedClient:
    """OpenAI-compatible chat client with bounded retries on 429/5xx."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 90.0,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self._timeout = timeout_s
        self._max_retries = max_retries
        self._sleep = sleep

    def complete(self, *, system: str, user: str, max_tokens: int) -> ChatResult:
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {self._key}"}
        for attempt in range(self._max_retries + 1):
            resp = httpx.post(self._url, json=body, headers=headers, timeout=self._timeout)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self._max_retries:
                wait = float(resp.headers.get("retry-after", 2 ** (attempt + 1)))
                self._sleep(min(wait, 60.0))
                continue
            if resp.status_code == 400:
                # json_validate_failed: the model produced something unusable; let the caller retry
                return ChatResult(text="", tokens_in=0, tokens_out=0)
            resp.raise_for_status()
            data = resp.json()
            usage = data.get("usage", {})
            return ChatResult(
                text=data["choices"][0]["message"].get("content") or "",
                tokens_in=int(usage.get("prompt_tokens", 0)),
                tokens_out=int(usage.get("completion_tokens", 0)),
            )
        raise RuntimeError("unreachable")


def draft_candidate(
    factory: PromptFactory,
    client: ChatClient,
    candidate: Mapping[str, Any],
    tracker: CostTracker,
    *,
    max_tokens: int = MAX_TOKENS,
) -> dict[str, Any] | None:
    """Return a validated unchecked example, or None if the model never produced a valid one."""
    dim = str(candidate["dimension"])
    rubric_id = str(candidate.get("rubric_id", f"{dim}@v1"))
    evidence = [(e["evidence_id"], e["quote"]) for e in candidate["evidence"]]
    features = dict(candidate.get("features", {}))
    profile = candidate.get("profile")
    base: dict[str, Any] = {
        "factory": factory,
        "idea_id": str(candidate["idea_id"]),
        "idea": str(candidate["idea_text"]),
        "rubric_id": rubric_id,
        "evidence": evidence,
        "features": features,
        "profile": profile,
    }
    extra = {"draft_model": client.model, "source": candidate.get("source", "kb")}
    if not evidence:  # evidence or abstain: no call needed, the answer is fixed
        return make_example(
            score=None, confidence=0.0, cited=[], reason="no evidence", extra_meta=extra, **base
        )
    system, user = factory.build(rubric_id, base["idea"], evidence, features, profile)
    for _ in range(ATTEMPTS):
        result = client.complete(system=system, user=user, max_tokens=max_tokens)
        tracker.add(result.tokens_in, result.tokens_out)
        try:
            body = json.loads(result.text)
            if not isinstance(body, dict):
                continue
            ex = make_example(
                score=body.get("score"),
                confidence=body.get("confidence", -1),
                cited=body.get("evidence_ids", []),
                reason=str(body.get("reason", "")),
                extra_meta=extra,
                **base,
            )
            if set(body) != {"score", "confidence", "evidence_ids", "reason"}:
                continue
            validate_example(ex)
            return ex
        except (json.JSONDecodeError, ExampleError, TypeError, ValueError):
            continue
    return None


def run_drafting(
    factory: PromptFactory,
    client: ChatClient,
    candidates: Sequence[Mapping[str, Any]],
    *,
    out_path: Path,
    tracker: CostTracker,
    max_tokens: int = MAX_TOKENS,
    log: Callable[[str], None] = lambda _m: None,
) -> dict[str, Any]:
    """Append drafts to ``out_path``; already-drafted (idea, dimension) pairs are skipped."""
    done: set[tuple[str, str]] = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                m = json.loads(line)["meta"]
                done.add((m["idea_id"], m["dimension"]))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = dropped = 0
    for cand in candidates:
        key = (str(cand["idea_id"]), str(cand["dimension"]))
        if key in done:
            continue
        ex = draft_candidate(factory, client, cand, tracker, max_tokens=max_tokens)
        if ex is None:
            dropped += 1
            log(f"dropped {key}")
            continue
        with open(out_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(ex, ensure_ascii=False) + "\n")
        written += 1
        log(f"{written} drafted, ${tracker.cost_usd:.4f} so far")
    return {
        "written": written,
        "dropped": dropped,
        "calls": tracker.calls,
        "tokens_in": tracker.tokens_in,
        "tokens_out": tracker.tokens_out,
        "cost_usd": round(tracker.cost_usd, 6),
    }


def main(argv: Sequence[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="finetune.draft_labels")
    p.add_argument("--candidates", default="jobs/finetune/data/candidates.jsonl")
    p.add_argument("--out", default="jobs/finetune/data/drafts.jsonl")
    p.add_argument("--alias", default="hosted-default", help="alias in config/llm.yaml")
    p.add_argument("--limit", type=int, default=0)
    a = p.parse_args(argv)
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise SystemExit("GROQ_API_KEY is not set (put it in the git-ignored .env and export it)")
    root = repo_root()
    prices = load_prices(root / "config" / "llm.yaml", a.alias)
    client = HostedClient(base_url=prices["base_url"], api_key=key, model=prices["model"])
    tracker = CostTracker(
        price_in_per_mtok=prices["price_in_per_mtok"],
        price_out_per_mtok=prices["price_out_per_mtok"],
    )
    cands = [
        json.loads(line)
        for line in Path(a.candidates).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if a.limit:
        cands = cands[: a.limit]
    factory = PromptFactory(root / "rubrics", root / "prompts")
    summary = run_drafting(factory, client, cands, out_path=Path(a.out), tracker=tracker, log=print)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
