"""Verdict tuning (P10-T03): a bounded grid over thresholds and weights with k-fold validation.

Offline tool. It re-runs the pure M11 engine in-process on labelled cases and proposes new
numbers; it never decides a verdict itself and never edits ``config/verdict.yaml``. A change is
only recommended when it helps on held-out folds (risk 10: tuning must not overfit 40 items), and
a false GO (system GO, human NO-GO) is the most expensive error.
"""

import json
import re
from collections.abc import Sequence
from itertools import product
from pathlib import Path
from typing import Any, Literal

from svc_verdict.models.inputs import EngineInput
from svc_verdict.services.engine import run_core
from svc_verdict.services.engine_config import EngineConfig, Thresholds

from common.schemas.base import Message
from common.schemas.types import DIMENSIONS
from evaluation.metrics import build_report

Verdict = Literal["GO", "PIVOT", "NO-GO", "INSUFFICIENT_EVIDENCE"]

FALSE_GO_COST = 5.0
MIN_CASES = 12  # below this a proposal is noise
MIN_GOAL_CASES = 10  # per goal, before its weights are touched
MIN_GAIN = 0.05  # relative held-out cost reduction required to recommend a change
BOUNDS: dict[str, tuple[float, float]] = {
    "T_go": (60.0, 85.0),
    "T_no": (35.0, 60.0),
    "floor": (1.0, 2.0),
}
MIN_GAP = 5.0
WEIGHT_BAND = (0.5, 1.5)  # a weight may move to 50%..150% of its preset
WEIGHT_STEP = 1.0
MIN_WEIGHT = 2.0

# (human, system) -> cost. Optimistic errors cost more; the worst is a false GO.
_COST: dict[tuple[str, str], float] = {
    ("NO-GO", "GO"): FALSE_GO_COST,
    ("PIVOT", "GO"): 2.0,
    ("GO", "NO-GO"): 2.0,
    ("GO", "PIVOT"): 1.0,
    ("PIVOT", "NO-GO"): 1.0,
    ("NO-GO", "PIVOT"): 1.0,
}


class TuningCase(Message):
    id: str
    input: EngineInput
    human_verdict: Verdict


def case_cost(human: str, system: str) -> float:
    if human == system:
        return 0.0
    return _COST.get((human, system), 1.0)


def load_cases(path: Path) -> list[TuningCase]:
    cases: list[TuningCase] = []
    seen: set[str] = set()
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        case = TuningCase.model_validate(json.loads(line))
        if case.id in seen:
            raise ValueError(f"duplicate case id {case.id!r}")
        seen.add(case.id)
        cases.append(case)
    return cases


def evaluate_cases(cases: Sequence[TuningCase], cfg: EngineConfig) -> list[str]:
    return [run_core(c.input, cfg).verdict for c in cases]


def total_cost(cases: Sequence[TuningCase], cfg: EngineConfig) -> float:
    return sum(
        case_cost(c.human_verdict, v)
        for c, v in zip(cases, evaluate_cases(cases, cfg), strict=True)
    )


def false_go_count(cases: Sequence[TuningCase], cfg: EngineConfig) -> int:
    verdicts = evaluate_cases(cases, cfg)
    return sum(
        1 for c, v in zip(cases, verdicts, strict=True) if c.human_verdict == "NO-GO" and v == "GO"
    )


def with_params(cfg: EngineConfig, *, t_go: float, t_no: float, floor: float) -> EngineConfig:
    thresholds = Thresholds(T_go=t_go, T_no=t_no, floor=floor)
    return cfg.model_copy(update={"thresholds": thresholds})


def _frange(lo: float, hi: float, step: float) -> list[float]:
    n = round((hi - lo) / step)
    return [round(lo + i * step, 4) for i in range(n + 1)]


def threshold_grid(cfg: EngineConfig) -> list[tuple[float, float, float]]:
    go = _frange(*BOUNDS["T_go"], 2.5)
    no = _frange(*BOUNDS["T_no"], 2.5)
    floors = _frange(*BOUNDS["floor"], 0.25)
    t = cfg.thresholds
    grid = {(a, b, f) for a, b, f in product(go, no, floors) if a - b >= MIN_GAP}
    grid.add((t.T_go, t.T_no, t.floor))  # the current values are always a candidate
    return sorted(grid)


def _distance(cfg: EngineConfig, params: tuple[float, float, float]) -> float:
    t = cfg.thresholds
    return abs(params[0] - t.T_go) + abs(params[1] - t.T_no) + 10 * abs(params[2] - t.floor)


def tune_thresholds(cases: Sequence[TuningCase], cfg: EngineConfig) -> EngineConfig:
    """Lowest total cost; ties go to the candidate closest to the current numbers."""
    best: tuple[float, float, tuple[float, float, float]] | None = None
    for params in threshold_grid(cfg):
        cost = total_cost(cases, with_params(cfg, t_go=params[0], t_no=params[1], floor=params[2]))
        key = (cost, _distance(cfg, params), params)
        if best is None or key < best:
            best = key
    assert best is not None
    t_go, t_no, floor = best[2]
    return with_params(cfg, t_go=t_go, t_no=t_no, floor=floor)


def _with_weights(cfg: EngineConfig, goal: str, weights: dict[str, float]) -> EngineConfig:
    return cfg.model_copy(update={"weights": {**cfg.weights, goal: weights}})


def tune_weights(cases: Sequence[TuningCase], cfg: EngineConfig, goal: str) -> EngineConfig:
    """Coordinate descent in 1-point moves inside a band around the preset; sums stay at 100."""
    mine = [c for c in cases if c.input.profile.goal == goal]
    if len(mine) < MIN_GOAL_CASES:
        return cfg
    base: dict[str, float] = {d: cfg.weights[goal][d] for d in DIMENSIONS}  # type: ignore[index]
    lo = {d: max(MIN_WEIGHT, WEIGHT_BAND[0] * base[d]) for d in DIMENSIONS}
    hi = {d: WEIGHT_BAND[1] * base[d] for d in DIMENSIONS}
    current: dict[str, float] = dict(base)
    current_cost = total_cost(mine, cfg)
    for _ in range(20):
        improved = False
        for give, take in product(DIMENSIONS, DIMENSIONS):
            if give == take:
                continue
            if (
                current[give] - WEIGHT_STEP < lo[give] - 1e-9
                or current[take] + WEIGHT_STEP > hi[take] + 1e-9
            ):
                continue
            trial: dict[str, float] = dict(current)
            trial[give] -= WEIGHT_STEP
            trial[take] += WEIGHT_STEP
            cost = total_cost(mine, _with_weights(cfg, goal, trial))
            if cost < current_cost - 1e-9:
                current, current_cost, improved = trial, cost, True
        if not improved:
            break
    if current == base:
        return cfg
    return _with_weights(cfg, goal, current)


def tune_all(cases: Sequence[TuningCase], cfg: EngineConfig) -> EngineConfig:
    tuned = tune_thresholds(cases, cfg)
    for goal in ("startup", "portfolio", "course_project"):
        tuned = tune_weights(cases, tuned, goal)
    return tuned


def kfold(cases: Sequence[TuningCase], k: int) -> list[list[int]]:
    """Deterministic folds of case indexes, stratified by human verdict."""
    if k < 2:
        raise ValueError("k-fold needs at least 2 folds")
    if len(cases) < 2:
        raise ValueError("k-fold needs at least 2 cases")
    k = min(k, len(cases))
    order = sorted(range(len(cases)), key=lambda i: (cases[i].human_verdict, cases[i].id))
    folds: list[list[int]] = [[] for _ in range(k)]
    for position, index in enumerate(order):
        folds[position % k].append(index)
    return [sorted(f) for f in folds]


def cross_validate(cases: Sequence[TuningCase], cfg: EngineConfig, k: int = 5) -> dict[str, Any]:
    folds = kfold(cases, k)
    default_cost = tuned_cost = 0.0
    default_fg = tuned_fg = 0
    for held in folds:
        held_set = set(held)
        train = [c for i, c in enumerate(cases) if i not in held_set]
        test = [cases[i] for i in held]
        tuned = tune_all(train, cfg)
        default_cost += total_cost(test, cfg)
        tuned_cost += total_cost(test, tuned)
        default_fg += false_go_count(test, cfg)
        tuned_fg += false_go_count(test, tuned)
    gain = (default_cost - tuned_cost) / default_cost if default_cost > 0 else 0.0
    return {
        "folds": len(folds),
        "default_cost": default_cost,
        "tuned_cost": tuned_cost,
        "default_false_go": default_fg,
        "tuned_false_go": tuned_fg,
        "gain": round(gain, 4),
    }


def bump_version(version: str) -> str:
    match = re.fullmatch(r"(.*?)(\d+)", version)
    return f"{match.group(1)}{int(match.group(2)) + 1}" if match else f"{version}2"


def _report(cases: Sequence[TuningCase], cfg: EngineConfig) -> dict[str, Any]:
    return build_report([c.human_verdict for c in cases], evaluate_cases(cases, cfg)).to_dict()


def propose(cases: Sequence[TuningCase], cfg: EngineConfig, k: int = 5) -> dict[str, Any]:
    """A proposal for ``config/verdict.yaml``; applying it is a reviewed step."""
    t = cfg.thresholds
    keep: dict[str, Any] = {
        "recommend": "keep_defaults",
        "thresholds": {"T_go": t.T_go, "T_no": t.T_no, "floor": t.floor},
        "weights": {g: dict(w) for g, w in cfg.weights.items()},
        "weights_version": cfg.weights_version,
        "thresholds_version": cfg.thresholds_version,
        "cv": None,
    }
    if len(cases) < MIN_CASES:
        report = _report(cases, cfg) if cases else None
        reason = f"too few labelled cases ({len(cases)} < {MIN_CASES})"
        return {**keep, "reason": reason, "before": report, "after": report}
    cv = cross_validate(cases, cfg, k)
    before = _report(cases, cfg)
    if cv["gain"] < MIN_GAIN or cv["tuned_false_go"] > cv["default_false_go"]:
        return {**keep, "cv": cv, "before": before, "after": before,
                "reason": "no held-out gain: the current values stay"}  # fmt: skip
    tuned = tune_all(cases, cfg)
    nt = tuned.thresholds
    return {
        "recommend": "apply",
        "reason": f"held-out cost fell {cv['gain']:.0%} with no extra false GO",
        "thresholds": {"T_go": nt.T_go, "T_no": nt.T_no, "floor": nt.floor},
        "weights": {g: dict(w) for g, w in tuned.weights.items()},
        "weights_version": bump_version(cfg.weights_version),
        "thresholds_version": bump_version(cfg.thresholds_version),
        "cv": cv,
        "before": before,
        "after": _report(cases, tuned),
    }
