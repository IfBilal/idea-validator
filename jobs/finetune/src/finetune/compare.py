"""Base vs fine-tuned vs hosted on the hand-checked test set (P8-T08, docs/harness/11 §5).

Every model receives the byte-identical prompt and the same ``max_tokens``. Nothing here tunes on
the test set: the comparison runs once per invocation and the numbers are reported as they are.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .metrics import latency_stats, score_outputs
from .schema import prompt_evidence_ids

MAX_TOKENS = 800


@dataclass(frozen=True)
class Generation:
    text: str
    seconds: float
    cost_usd: float = 0.0


class Model(Protocol):
    def generate(self, *, system: str, user: str, max_tokens: int) -> Generation: ...


def _check_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise ValueError("the test set is empty")
    for row in rows:
        meta = row["meta"]
        if meta.get("synthetic"):
            raise ValueError("synthetic example in the test set")
        if not meta.get("hand_checked"):
            raise ValueError(
                f"example {meta['idea_id']} is not hand-checked: test set is human-only"
            )


def compare_models(
    rows: Sequence[Mapping[str, Any]],
    models: Mapping[str, Model],
    max_tokens: int = MAX_TOKENS,
    *,
    seed: int = 42,
) -> dict[str, Any]:
    _check_rows(rows)
    result: dict[str, Any] = {"n_test": len(rows), "max_tokens": max_tokens, "models": {}}
    for name, model in models.items():
        items: list[dict[str, Any]] = []
        seconds: list[float] = []
        cost = 0.0
        for row in rows:
            system, user = row["messages"][0]["content"], row["messages"][1]["content"]
            gen = model.generate(system=system, user=user, max_tokens=max_tokens)
            seconds.append(gen.seconds)
            cost += gen.cost_usd
            human = row["meta"]["final_label"]
            items.append(
                {
                    "dimension": row["meta"]["dimension"],
                    "human": 0.0 if human is None else float(human),
                    "provided": prompt_evidence_ids(user),
                    "output": gen.text,
                }
            )
        metrics = score_outputs(items, seed=seed)
        metrics["latency_s"] = latency_stats(seconds)
        metrics["cost_per_score_usd"] = cost / len(rows)
        result["models"][name] = metrics
    return result


def failure_case(
    result: Mapping[str, Any], *, train_counts: Mapping[str, Mapping[str, int]]
) -> dict[str, Any]:
    """The dimension where the fine-tuned MAE is worst relative to hosted (or base as fallback).

    Reports ``found: False`` when fine-tuning was never worse: the table is never massaged.
    """
    models = result["models"]
    fine = models.get("finetuned", {}).get("per_dimension", {})
    for other_name in ("hosted", "base"):
        other = models.get(other_name, {}).get("per_dimension", {})
        worst: tuple[float, str] | None = None
        for dim, stats in fine.items():
            f, o = stats.get("mae"), other.get(dim, {}).get("mae")
            if f is None or o is None:
                continue
            gap = f - o
            if gap > 0 and (worst is None or gap > worst[0]):
                worst = (gap, dim)
        if worst is not None:
            dim = worst[1]
            return {
                "found": True,
                "dimension": dim,
                "compared_with": other_name,
                "finetuned_mae": fine[dim]["mae"],
                f"{other_name}_mae": other[dim]["mae"],
                "n": fine[dim]["n"],
                "train_examples_per_level": dict(train_counts.get(dim, {})),
            }
    return {
        "found": False,
        "note": "fine-tuning was not worse than hosted or base on any dimension",
    }


def _fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render_markdown(result: Mapping[str, Any]) -> str:
    head = (
        "| Model | MAE vs human (95% CI) | Within 1 point | Valid JSON | Real evidence ids "
        "| Latency p50/p95 (s) | Cost / score (USD) |\n|---|---|---|---|---|---|---|"
    )
    lines = [head]
    for name, m in result["models"].items():
        lo, hi = m["mae_ci95"]
        lat = m["latency_s"]
        lines.append(
            f"| {name} | {_fmt(m['mae'])} [{_fmt(lo)}, {_fmt(hi)}] | {_fmt(m['within_1'])} "
            f"| {_fmt(m['valid_json_rate'])} | {_fmt(m['real_evidence_rate'])} "
            f"| {_fmt(lat['p50'])}/{_fmt(lat['p95'])} | {m['cost_per_score_usd']:.5f} |"
        )
    lines.append("")
    lines.append(f"MAE columns: lower is better. n = {result['n_test']} hand-checked examples.")
    dims = sorted({d for m in result["models"].values() for d in m["per_dimension"]})
    lines += ["", "Per-dimension MAE:", "", "| Dimension | " + " | ".join(result["models"]) + " |"]
    lines.append("|---|" + "---|" * len(result["models"]))
    for d in dims:
        cells = [_fmt(m["per_dimension"].get(d, {}).get("mae")) for m in result["models"].values()]
        lines.append(f"| {d} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


class LlamaServerModel:
    """A llama.cpp ``llama-server`` over HTTP, optionally with JSON-schema constrained decoding."""

    def __init__(self, base_url: str, *, schema: dict[str, Any] | None = None) -> None:
        self._url = base_url.rstrip("/") + "/v1/chat/completions"
        self._schema = schema

    def generate(self, *, system: str, user: str, max_tokens: int) -> Generation:
        import time

        body: dict[str, Any] = {
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self._schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "score", "strict": True, "schema": self._schema},
            }
        req = urllib.request.Request(  # noqa: S310 - localhost benchmark server
            self._url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:  # noqa: S310
                data = json.load(resp)
            text = data["choices"][0]["message"].get("content") or ""
        except (urllib.error.URLError, KeyError, ValueError):
            text = ""
        return Generation(text=text, seconds=time.perf_counter() - start)


def write_outputs(result: Mapping[str, Any], json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
