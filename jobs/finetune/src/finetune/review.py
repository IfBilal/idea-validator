"""Human review CLI for draft labels (P8-T03): ``python -m finetune.review --reviewer taimoor``.

Only a person at a terminal can mark an example hand-checked: the CLI refuses a non-interactive
stdin, and ``hand_checked`` is written in exactly one place (``merge_reviews``), from review records
that carry ``checked_by``, ``checked_at``, ``original_label`` and ``final_label``. Nothing here
auto-accepts, simulates a reviewer or fixes a count.
"""

import argparse
import copy
import json
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .prompting import target_json
from .schema import on_grid, parse_target, prompt_evidence_ids

REVIEWERS = ("taimoor", "bilal")


class NotInteractiveError(RuntimeError):
    """Reviews must be typed by a person at a terminal."""


class ReviewRecord(BaseModel):
    idea_id: str
    dimension: str
    checked_by: str
    checked_at: str
    original_label: float | None
    final_label: float | None


def example_key(idea_id: str, dimension: str) -> str:
    return f"{idea_id}::{dimension}"


def require_interactive(*, is_tty: bool) -> None:
    if not is_tty:
        raise NotInteractiveError("review needs a person at a terminal (stdin is not a TTY)")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _existing(out_path: Path) -> set[str]:
    if not out_path.exists():
        return set()
    keys = set()
    for line in out_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            keys.add(example_key(rec["idea_id"], rec["dimension"]))
    return keys


def _show(ex: dict[str, Any], position: str, print_fn: Callable[[str], None]) -> None:
    meta = ex["meta"]
    user = ex["messages"][1]["content"]
    draft = parse_target(ex["messages"][2]["content"])
    print_fn(f"\n=== {position}  dimension: {meta['dimension']}  rubric: {meta['rubric_version']}")
    print_fn(f"IDEA: {meta.get('idea_text', '(see prompt)')}")
    print_fn("EVIDENCE:\n" + user.split("EVIDENCE:", 1)[-1].strip())
    print_fn(
        f"DRAFT: score={draft.score} confidence={draft.confidence} "
        f"ids={draft.evidence_ids}\n       reason: {draft.reason}"
    )


def _ask_score(input_fn: Callable[[str], str], *, can_score: bool) -> tuple[bool, float | None]:
    """Ask for a score on the 0.5 grid, or `n` for abstain. Returns (valid, value)."""
    while True:
        raw = input_fn("new score 0-5 in 0.5 steps (n = abstain): ").strip().lower()
        if raw == "n":
            return True, None
        try:
            value = float(raw)
        except ValueError:
            continue
        if on_grid(value) and can_score:
            return True, value


def review_session(
    examples: Sequence[dict[str, Any]],
    *,
    reviewer: str,
    input_fn: Callable[[str], str],
    print_fn: Callable[[str], None],
    out_path: Path,
    now_fn: Callable[[], str] = _now,
) -> int:
    if reviewer not in REVIEWERS:
        raise ValueError(f"reviewer must be one of {REVIEWERS}")
    done = _existing(out_path)
    todo = [
        e for e in examples if example_key(e["meta"]["idea_id"], e["meta"]["dimension"]) not in done
    ]
    count = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for i, ex in enumerate(todo, 1):
        _show(ex, f"{i}/{len(todo)}", print_fn)
        draft = parse_target(ex["messages"][2]["content"])
        can_score = bool(prompt_evidence_ids(ex["messages"][1]["content"]))
        final: float | None
        while True:
            choice = input_fn("[a]ccept  [e]dit  [s]kip  [q]uit > ").strip().lower()
            if choice in {"a", "e", "s", "q"}:
                break
        if choice == "q":
            break
        if choice == "s":
            continue
        if choice == "a":
            final = draft.score
        else:
            _, final = _ask_score(input_fn, can_score=can_score)
        rec = ReviewRecord(
            idea_id=ex["meta"]["idea_id"],
            dimension=ex["meta"]["dimension"],
            checked_by=reviewer,
            checked_at=now_fn(),
            original_label=draft.score,
            final_label=final,
        )
        with open(out_path, "a", encoding="utf-8") as handle:
            handle.write(rec.model_dump_json() + "\n")
        count += 1
    return count


def merge_reviews(
    examples: Sequence[dict[str, Any]], records: Sequence[ReviewRecord]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply reviews. Returns (examples, conflicting keys).

    An example becomes ``hand_checked`` only if at least one person reviewed it and all reviewers
    agree on the final label; disagreement leaves it unchecked and lists it as a conflict.
    """
    by_key: dict[str, list[ReviewRecord]] = defaultdict(list)
    for rec in records:
        by_key[example_key(rec.idea_id, rec.dimension)].append(rec)
    merged: list[dict[str, Any]] = []
    conflicts: list[str] = []
    for original in examples:
        ex = copy.deepcopy(original)
        key = example_key(ex["meta"]["idea_id"], ex["meta"]["dimension"])
        recs = by_key.get(key, [])
        if not recs:
            merged.append(ex)
            continue
        if len({r.final_label for r in recs}) > 1:
            conflicts.append(key)
            merged.append(ex)
            continue
        final = recs[0].final_label
        draft = parse_target(ex["messages"][2]["content"])
        if final != draft.score:
            ids = [] if final is None else (draft.evidence_ids or ex["meta"]["evidence_ids"][:1])
            ex["messages"][2]["content"] = target_json(
                final, 0.0 if final is None else draft.confidence, ids, draft.reason
            )
        ex["meta"].update(
            level=final,
            hand_checked=True,
            checked_by="+".join(sorted({r.checked_by for r in recs})),
            checked_at=max(r.checked_at for r in recs),
            original_label=recs[0].original_label,
            final_label=final,
        )
        merged.append(ex)
    return merged, conflicts


def load_reviews(directory: Path) -> list[ReviewRecord]:
    out: list[ReviewRecord] = []
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(ReviewRecord.model_validate_json(line))
    return out


def main(argv: Sequence[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="finetune.review")
    p.add_argument("--reviewer", required=True, choices=REVIEWERS)
    p.add_argument("--drafts", default="jobs/finetune/data/drafts.jsonl")
    p.add_argument("--reviews-dir", default="jobs/finetune/data/reviews")
    args = p.parse_args(argv)
    require_interactive(is_tty=sys.stdin.isatty())
    examples = [
        json.loads(line)
        for line in Path(args.drafts).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    out = Path(args.reviews_dir) / f"{args.reviewer}.jsonl"
    n = review_session(
        examples, reviewer=args.reviewer, input_fn=input, print_fn=print, out_path=out
    )
    print(f"{n} reviews saved to {out}")


if __name__ == "__main__":
    main()
