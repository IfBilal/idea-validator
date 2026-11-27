"""Evaluation metrics (11 §5): MAE vs human, within-1, valid JSON, real ids, latency."""

import json
import math
import random
from collections.abc import Mapping, Sequence
from typing import Any

from .schema import EVIDENCE_ID, TARGET_KEYS, on_grid


def _parse(output: str) -> dict[str, Any] | None:
    text = output.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        body = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(body, dict) or set(body) != TARGET_KEYS:
        return None
    score, conf, ids, reason = (
        body["score"],
        body["confidence"],
        body["evidence_ids"],
        body["reason"],
    )
    if score is not None and (
        isinstance(score, bool) or not isinstance(score, int | float) or not on_grid(float(score))
    ):
        return None
    if isinstance(conf, bool) or not isinstance(conf, int | float) or not 0 <= conf <= 1:
        return None
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return None
    if not isinstance(reason, str):
        return None
    return body


def bootstrap_ci(
    values: Sequence[float], *, seed: int, n_boot: int = 2000, alpha: float = 0.05
) -> tuple[float | None, float | None]:
    """Percentile bootstrap confidence interval of the mean (deterministic given ``seed``)."""
    if not values:
        return None, None
    if len(values) == 1:
        return float(values[0]), float(values[0])
    rng = random.Random(seed)  # noqa: S311 - statistics, not security
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot))
    lo = means[int((alpha / 2) * n_boot)]
    hi = means[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return lo, hi


def latency_stats(samples: Sequence[float]) -> dict[str, float | None]:
    if not samples:
        return {"p50": None, "p95": None}
    ordered = sorted(samples)

    def pct(q: float) -> float:
        pos = (len(ordered) - 1) * q
        lo, hi = math.floor(pos), math.ceil(pos)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)

    return {"p50": pct(0.5), "p95": pct(0.95)}


def _mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def score_outputs(items: Sequence[Mapping[str, Any]], *, seed: int = 42) -> dict[str, Any]:
    """Each item: dimension, human (float), provided (evidence ids shown), output (raw text)."""
    n = len(items)
    valid = 0
    abstain = 0
    ids_ok = 0
    ids_total = 0
    errors: list[float] = []
    by_dim: dict[str, dict[str, list[float]]] = {}
    dim_n: dict[str, int] = {}
    for item in items:
        dim = str(item["dimension"])
        dim_n[dim] = dim_n.get(dim, 0) + 1
        body = _parse(str(item["output"]))
        if body is None:
            continue
        valid += 1
        if body["score"] is None:
            abstain += 1
            continue
        ids_total += 1
        provided = set(item["provided"])
        if body["evidence_ids"] and all(
            i in provided and EVIDENCE_ID.match(i) for i in body["evidence_ids"]
        ):
            ids_ok += 1
        err = abs(float(body["score"]) - float(item["human"]))
        errors.append(err)
        by_dim.setdefault(dim, {"err": []})["err"].append(err)
    lo, hi = bootstrap_ci(errors, seed=seed)
    per_dimension = {
        dim: {
            "n": dim_n[dim],
            "mae": _mean(by_dim.get(dim, {}).get("err", [])),
            "within_1": _mean([1.0 if e <= 1 else 0.0 for e in by_dim.get(dim, {}).get("err", [])]),
        }
        for dim in sorted(dim_n)
    }
    return {
        "n": n,
        "valid_json_rate": valid / n if n else None,
        "abstain_rate": abstain / n if n else None,
        "real_evidence_rate": ids_ok / ids_total if ids_total else None,
        "mae": _mean(errors),
        "mae_ci95": [lo, hi],
        "within_1": _mean([1.0 if e <= 1 else 0.0 for e in errors]),
        "per_dimension": per_dimension,
    }
