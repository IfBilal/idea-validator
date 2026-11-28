"""Turn the completed REVIEW_WORKSHEET.md into eval/labelled/labelled_set.jsonl.

Usage: python eval/labelled/make_labelled_set.py [--worksheet PATH] [--candidates PATH] [--out PATH]

Refuses (exit 1) on empty or invalid labels, unresolved differences, or fewer than 10 items per
category. Warns (still runs) when a human's column equals the AI draft on every row.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
VERDICTS = ("GO", "PIVOT", "NO-GO", "INSUFFICIENT_EVIDENCE")
CATEGORIES = ("clear_go", "clear_nogo", "borderline", "thin_evidence")
DERIVED = {
    "GO": "clear_go",
    "NO-GO": "clear_nogo",
    "PIVOT": "borderline",
    "INSUFFICIENT_EVIDENCE": "thin_evidence",
}
MIN_PER_CATEGORY = 10
HUMANS = ("bilal", "taimoor")


class WorksheetError(Exception):
    pass


def parse_worksheet(text: str) -> list[dict[str, str]]:
    header: list[str] | None = None
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            if cells and cells[0] == "id":
                header = cells
            continue
        if set(line.replace("|", "").strip()) <= set("-: "):
            continue
        if len(cells) != len(header):
            raise WorksheetError(f"row has {len(cells)} cells, expected {len(header)}: {line[:60]}")
        rows.append(dict(zip(header, cells, strict=True)))
    if header is None:
        raise WorksheetError("worksheet table header not found")
    return rows


def build(
    rows: list[dict[str, str]], candidates: dict[str, dict], warn: list[str]
) -> list[dict[str, object]]:
    errors: list[str] = []
    out: list[dict[str, object]] = []
    for r in rows:
        rid = r["id"]
        if rid not in candidates:
            errors.append(f"{rid}: not in candidates.jsonl")
            continue
        labels = {h: r.get(h, "") for h in HUMANS}
        for h, v in labels.items():
            if v not in VERDICTS:
                errors.append(f"{rid}: {h} label {v!r} is empty or not one of {VERDICTS}")
        if any(v not in VERDICTS for v in labels.values()):
            continue
        resolved = r.get("resolved", "")
        if labels["bilal"] == labels["taimoor"]:
            final, by = labels["bilal"], "agreement"
            if resolved and resolved != final:
                errors.append(f"{rid}: humans agree on {final} but resolved says {resolved}")
                continue
        else:
            if resolved not in VERDICTS:
                errors.append(f"{rid}: labels differ, `resolved` must be one of {VERDICTS}")
                continue
            if not r.get("resolved_by", ""):
                errors.append(f"{rid}: labels differ, `resolved_by` is empty")
                continue
            final, by = resolved, r["resolved_by"]
        category = r.get("category", "") or DERIVED[final]
        if category not in CATEGORIES:
            errors.append(f"{rid}: category {category!r} not in {CATEGORIES}")
            continue
        out.append(
            {
                "id": rid,
                "idea_text": candidates[rid]["idea_text"],
                "profile": candidates[rid]["profile"],
                "human_verdict": final,
                "category": category,
                "labels": labels,
                "resolved_by": by,
                "notes": r.get("notes", ""),
            }
        )
    missing = set(candidates) - {r["id"] for r in rows}
    if missing:
        errors.append(f"candidates missing from worksheet: {sorted(missing)}")
    if errors:
        raise WorksheetError("\n".join(errors))
    counts = Counter(str(o["category"]) for o in out)
    short = {c: counts.get(c, 0) for c in CATEGORIES if counts.get(c, 0) < MIN_PER_CATEGORY}
    if short:
        raise WorksheetError(
            f"fewer than {MIN_PER_CATEGORY} items in categories {short}; add candidates first"
        )
    for h in HUMANS:
        if all(r.get(h) == r.get("ai_draft") for r in rows):
            warn.append(f"{h}'s column equals the AI draft on every row: possible anchoring")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worksheet", type=Path, default=HERE / "REVIEW_WORKSHEET.md")
    ap.add_argument("--candidates", type=Path, default=HERE / "candidates.jsonl")
    ap.add_argument("--out", type=Path, default=HERE / "labelled_set.jsonl")
    args = ap.parse_args(argv)
    candidates = {
        d["id"]: d
        for d in (json.loads(x) for x in args.candidates.read_text().splitlines() if x.strip())
    }
    warn: list[str] = []
    try:
        rows = parse_worksheet(args.worksheet.read_text())
        out = build(rows, candidates, warn)
    except WorksheetError as e:
        print(f"REFUSED:\n{e}", file=sys.stderr)
        return 1
    for w in warn:
        print(f"WARNING: {w}", file=sys.stderr)
    args.out.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in out))
    print(f"wrote {len(out)} items to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
