"""Aggregate item rows into the ``eval/results/<timestamp>.json`` document.

``n``, ``agreement``, ``kappa``, ``confusion_matrix`` and ``false_go`` come from
``evaluation.metrics`` over the items that produced a verdict. Items that errored are reported
(``n_errors``, ``errors``, ``agreement_incl_errors``) and never leave the count silently.
"""

import math
from collections import Counter
from collections.abc import Sequence
from typing import Any

from evaluation.behaviour import CHECKS
from evaluation.metrics import LABELS, build_report

Row = dict[str, Any]


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _p95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)], 3)


def behaviour_rates(ok_rows: Sequence[Row]) -> tuple[dict[str, float], dict[str, int]]:
    """Share of applicable items where each check held; 1.0 when no item applies (see counts)."""
    rates: dict[str, float] = {}
    applicable: dict[str, int] = {}
    for name in CHECKS:
        values = [r["behaviour"][name] for r in ok_rows if r["behaviour"].get(name) is not None]
        applicable[name] = len(values)
        rates[name] = round(sum(1 for v in values if v) / len(values), 4) if values else 1.0
    return rates, applicable


def build_results(rows: Sequence[Row], meta: dict[str, Any]) -> dict[str, Any]:
    ok = [r for r in rows if r["status"] == "ok"]
    errors = Counter(r["error"] for r in rows if r["status"] != "ok")
    total = len(rows)
    out: dict[str, Any] = {**meta, "n_total": total, "n_errors": total - len(ok)}
    if ok:
        report = build_report([r["human"] for r in ok], [r["system"] for r in ok]).to_dict()
        hits = sum(1 for r in ok if r["human"] == r["system"])
    else:
        report = {
            "n": 0,
            "agreement": 0.0,
            "kappa": 0.0,
            "labels": list(LABELS),
            "confusion_matrix": [[0] * len(LABELS) for _ in LABELS],
            "false_go": 0,
            "false_go_rate": 0.0,
            "optimistic_go": 0,
            "per_class": {},
        }
        hits = 0
    rates, applicable = behaviour_rates(ok)
    agent_totals: dict[str, list[float]] = {}
    for r in ok:
        for agent, ms in r.get("agent_latency_ms", {}).items():
            agent_totals.setdefault(agent, []).append(float(ms))
    costs = [float(r["cost_usd"]) for r in ok if r.get("cost_usd") is not None]
    latencies = [float(r["latency_s"]) for r in ok]
    out.update(report)
    out.update(
        {
            "agreement_incl_errors": round(hits / total, 4) if total else 0.0,
            "error_rate": round((total - len(ok)) / total, 4) if total else 0.0,
            "errors": dict(sorted(errors.items())),
            "behaviour": rates,
            "behaviour_applicable": applicable,
            "traces_available": sum(1 for r in ok if r.get("trace_available")),
            "cost_per_verdict_usd": _mean(costs),
            "latency_s": {"mean": _mean(latencies), "p95": _p95(latencies)},
            "agent_latency_ms": {a: _mean(v) for a, v in sorted(agent_totals.items())},
            "fallbacks": sum(int(r.get("fallbacks", 0)) for r in ok),
            "cache_hits": sum(1 for r in ok if r.get("cache_hit")),
            "rows": list(rows),
        }
    )
    return out
