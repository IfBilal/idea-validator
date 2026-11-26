"""Reference implementation of the M11 Verdict Engine rules (Description §5, Figure 4).

PURPOSE: executable specification used to generate/verify `test_vectors.json`.
The production engine lives in services/verdict/ and MUST produce identical outputs on every vector.
Pure Python, no LLM, no network, no randomness, no clock -> same input gives same output.

Run:  python -I eval/verdict/reference_engine.py   (prints the worked example and regenerates vectors)
"""
from __future__ import annotations

import json
import math
import sys
from copy import deepcopy
from pathlib import Path

DIMS = ["demand_pain", "competition", "differentiation", "feasibility", "risk", "learning"]
FLOOR_DIMS = DIMS[:5]  # floor applies to the first five dimensions only (Description §5.3)
RANK = {"GO": 4, "PIVOT": 3, "INSUFFICIENT_EVIDENCE": 2, "NO-GO": 1}
EPS = 1e-9

DEFAULT_CONFIG = {
    "weights": {
        "startup": {"demand_pain": 25, "competition": 20, "differentiation": 20, "feasibility": 15, "risk": 15, "learning": 5},
        "portfolio": {"demand_pain": 15, "competition": 15, "differentiation": 15, "feasibility": 25, "risk": 10, "learning": 20},
        "course_project": {"demand_pain": 10, "competition": 10, "differentiation": 15, "feasibility": 35, "risk": 10, "learning": 20},
    },
    "thresholds": {"T_go": 70, "T_no": 50, "floor": 1.5},
    "confidence": {"min": 0.5, "w_dims": 0.7, "w_sources": 0.3, "sources_full": 5},
    "gates": {"min_sources": 3, "min_platforms": 2, "duplicate_diff_override": 3.0},
    "challenge": {"score_step": 0.5, "score_max": 1.0, "conf_step": 0.1},
    "sensitivity": {"weight_delta": 0.20, "score_delta": 1.0},
    "whatif": {"step": 0.5, "max_candidates": 3},
    "versions": {"engine": "m11@v1", "rubrics": "r1", "profile_weights": "w1"},
}


def _r(x: float, n: int = 4) -> float:
    return round(x + 0.0, n)


def weighted_score(scores: dict[str, float], weights: dict[str, float]) -> float:
    """Sum of weight x score, scaled to 0..100 (weights add to 100, scores 0..5 -> divide by 5)."""
    return sum(weights[d] * scores[d] for d in DIMS) / 5.0


def shift_weights(weights: dict[str, float], dim: str, factor: float) -> dict[str, float]:
    """Move one weight by `factor` (e.g. -0.2) and share the difference among the others in proportion."""
    new = dict(weights)
    old = weights[dim]
    new[dim] = old * (1 + factor)
    diff = new[dim] - old
    rest = sum(weights[d] for d in DIMS if d != dim)
    for d in DIMS:
        if d != dim:
            new[d] = weights[d] - diff * (weights[d] / rest)
    return new


def overall_confidence(confs: list[float], n_sources: int, cfg: dict) -> float:
    c = cfg["confidence"]
    return c["w_dims"] * (sum(confs) / len(confs)) + c["w_sources"] * min(1.0, n_sources / c["sources_full"])


def weakest_dimension(scores: dict[str, float], weights: dict[str, float], floor: float) -> str:
    breached = [d for d in FLOOR_DIMS if scores[d] < floor - EPS]
    pool = breached or DIMS
    # lowest score; tie -> larger weight (bigger impact); tie -> fixed DIMS order
    return sorted(pool, key=lambda d: (scores[d], -weights[d], DIMS.index(d)))[0]


def _core(inp: dict, cfg: dict) -> dict:
    """Gates -> score -> confidence -> classification. No sensitivity / what-if."""
    profile = inp["profile"]
    g = inp["gate_inputs"]
    th = cfg["thresholds"]
    weights = inp.get("weights_override") or cfg["weights"][profile["goal"]]
    assert abs(sum(weights.values()) - 100) < 1e-6, "weights must add to 100"

    # 1. challenge adjustments (objections already accepted by the critic)
    scores, confs, abstained = {}, {}, []
    counts: dict[str, int] = {}
    for o in inp.get("objections_accepted", []):
        counts[o["dimension"]] = counts.get(o["dimension"], 0) + 1
    ch = cfg["challenge"]
    for s in inp["scores"]:
        d = s["dimension"]
        n = counts.get(d, 0)
        if s.get("score") is None:
            abstained.append(d)
            scores[d], confs[d] = None, 0.0
            continue
        scores[d] = max(0.0, min(5.0, s["score"] - min(ch["score_step"] * n, ch["score_max"])))
        confs[d] = max(0.0, min(1.0, s["confidence"] - ch["conf_step"] * n))

    gates = {"evidence": "not_evaluated", "feasibility": "not_evaluated", "risk": "not_evaluated", "duplicate": "not_evaluated"}
    out = {"gates": gates, "score": None, "confidence": None, "weakest_dimension": None, "reasons": [],
           "weights": weights, "adjusted_scores": scores, "adjusted_confidences": confs}

    def done(verdict: str, reason: str) -> dict:
        out["verdict"] = verdict
        out["reasons"].append(reason)
        return out

    # 2. hard gates, in order (Figure 4)
    gc = cfg["gates"]
    if g["n_sources"] < gc["min_sources"] or g["n_platforms"] < gc["min_platforms"]:
        gates["evidence"] = "fail"
        return done("INSUFFICIENT_EVIDENCE",
                    f"evidence gate: {g['n_sources']} of {gc['min_sources']} independent sources, "
                    f"{g['n_platforms']} of {gc['min_platforms']} platforms")
    gates["evidence"] = "pass"

    available = profile["hours_per_week"] * profile["weeks_available"] * profile["team_size"]
    if g["estimated_effort_hours"] > available + EPS:
        gates["feasibility"] = "fail"
        gap = g["estimated_effort_hours"] - available
        return done("NO-GO", f"feasibility gate: needs ~{g['estimated_effort_hours']:g} h, "
                             f"available {available:g} h (gap {gap:g} h)")
    gates["feasibility"] = "pass"

    if g.get("risk_blocker"):
        gates["risk"] = "fail"
        return done("NO-GO", f"risk gate: blocker - {g['risk_blocker']}")
    gates["risk"] = "pass"

    diff = scores.get("differentiation")
    if g.get("duplicate_of") and not (diff is not None and diff >= gc["duplicate_diff_override"] - EPS):
        gates["duplicate"] = "fail"
        return done("NO-GO", f"duplicate gate: existing product {g['duplicate_of']} has the same core features for the same user")
    gates["duplicate"] = "pass"

    # 3. abstention: a scorer without evidence abstains -> do not guess
    if abstained:
        return done("INSUFFICIENT_EVIDENCE", "abstained (no evidence): " + ", ".join(abstained))

    # 4. weighted score
    S = weighted_score(scores, weights)
    out["score"] = _r(S)

    # 5. confidence
    C = overall_confidence([confs[d] for d in DIMS], g["n_sources"], cfg)
    out["confidence"] = _r(C, 3)
    if C < cfg["confidence"]["min"] - EPS:
        low = [d for d in DIMS if confs[d] < 0.5]
        return done("INSUFFICIENT_EVIDENCE", f"confidence {C:.2f} < {cfg['confidence']['min']}; low: {', '.join(low) or 'sources'}")

    # 6. thresholds + floor
    floor_ok = all(scores[d] >= th["floor"] - EPS for d in FLOOR_DIMS)
    if S >= th["T_go"] - EPS and floor_ok:
        return done("GO", f"score {S:.2f} >= T_go {th['T_go']} and no dimension below floor {th['floor']}")
    weakest = weakest_dimension(scores, weights, th["floor"])
    out["weakest_dimension"] = weakest
    if S >= th["T_no"] - EPS:
        why = "floor breach" if not floor_ok else f"score {S:.2f} between T_no and T_go"
        return done("PIVOT", f"{why}; weakest dimension: {weakest}")
    return done("NO-GO", f"score {S:.2f} < T_no {th['T_no']}")


def sensitivity(inp: dict, cfg: dict, base_verdict: str) -> dict:
    """14 runs: each of 6 weights +/-20% (shared proportionally) + least-confident score +/-1."""
    weights = inp.get("weights_override") or cfg["weights"][inp["profile"]["goal"]]
    runs, flips = 0, []
    for d in DIMS:
        for f in (+cfg["sensitivity"]["weight_delta"], -cfg["sensitivity"]["weight_delta"]):
            v = deepcopy(inp)
            v["weights_override"] = shift_weights(weights, d, f)
            r = _core(v, cfg)
            runs += 1
            if r["verdict"] != base_verdict:
                flips.append({"change": f"weight {d} {'+' if f > 0 else '-'}{int(abs(f) * 100)}%",
                              "verdict": r["verdict"], "score": r["score"]})
    scored = [s for s in inp["scores"] if s.get("score") is not None]
    if scored:
        # least confident AFTER challenge adjustments; tie -> fixed DIMS order
        adj_conf = _core(inp, cfg)["adjusted_confidences"]
        lc = sorted(scored, key=lambda s: (adj_conf[s["dimension"]], DIMS.index(s["dimension"])))[0]
        for delta in (+cfg["sensitivity"]["score_delta"], -cfg["sensitivity"]["score_delta"]):
            v = deepcopy(inp)
            for s in v["scores"]:
                if s["dimension"] == lc["dimension"]:
                    s["score"] = max(0.0, min(5.0, s["score"] + delta))
            r = _core(v, cfg)
            runs += 1
            if r["verdict"] != base_verdict:
                flips.append({"change": f"score {lc['dimension']} {'+' if delta > 0 else ''}{delta:g}",
                              "verdict": r["verdict"], "score": r["score"]})
    return {"runs": runs, "flipped": len(flips), "flips": flips}


def evaluate(inp: dict, cfg: dict | None = None, with_whatif: bool = True) -> dict:
    cfg = cfg or DEFAULT_CONFIG
    core = _core(inp, cfg)
    scored_stage = core["score"] is not None and core["verdict"] != "INSUFFICIENT_EVIDENCE"
    if scored_stage:
        sens = sensitivity(inp, cfg, core["verdict"])
    else:
        sens = {"runs": 0, "flipped": 0, "flips": [], "note": "decided before scoring (gate / abstention / confidence)"}
    res = {
        "idea_id": inp.get("idea_id"), "version": inp.get("version"), "run_id": inp.get("run_id"),
        "verdict": core["verdict"],
        "stability": "BORDERLINE" if sens["flipped"] else "STABLE",
        "score": core["score"], "confidence": core["confidence"], "gates": core["gates"],
        "weights": {k: _r(v, 4) for k, v in core["weights"].items()},
        "thresholds": cfg["thresholds"],
        "weakest_dimension": core["weakest_dimension"],
        "reasons": core["reasons"],
        "adjusted_scores": core["adjusted_scores"],
        "sensitivity": sens,
        "versions": cfg["versions"],
    }
    if with_whatif:
        res["what_would_make_go"] = whatif(inp, cfg, res)
    return res


def whatif(inp: dict, cfg: dict, base: dict) -> list[dict]:
    """Smallest single change that improves the verdict; target a STABLE GO when possible."""
    if base["verdict"] == "GO" and base["stability"] == "STABLE":
        return []
    cands = []
    step = cfg["whatif"]["step"]
    for s in inp["scores"]:
        if s.get("score") is None:
            continue
        d, cur = s["dimension"], s["score"]
        k = 1
        while cur + k * step <= 5.0 + EPS:
            v = deepcopy(inp)
            for x in v["scores"]:
                if x["dimension"] == d:
                    x["score"] = cur + k * step
            r = evaluate(v, cfg, with_whatif=False)
            better = RANK[r["verdict"]] > RANK[base["verdict"]] or (
                r["verdict"] == base["verdict"] == "GO" and r["stability"] == "STABLE")
            if better:
                cands.append({"dimension": d, "from": cur, "to": cur + k * step, "delta": k * step,
                              "new_score": r["score"], "new_verdict": r["verdict"], "stable": r["stability"] == "STABLE"})
                break
            k += 1
    # gate-based suggestions
    p, g = inp["profile"], inp["gate_inputs"]
    if base["gates"]["feasibility"] == "fail":
        need = math.ceil(g["estimated_effort_hours"] / (p["weeks_available"] * p["team_size"]))
        cands.append({"dimension": "feasibility_gate", "field": "hours_per_week", "from": p["hours_per_week"], "to": need,
                      "alt": f"or cut scope by {g['estimated_effort_hours'] - p['hours_per_week'] * p['weeks_available'] * p['team_size']:g} h",
                      "delta": None, "new_verdict": "re-evaluate", "stable": None})
    cands.sort(key=lambda c: (c["delta"] if c["delta"] is not None else 99, not c["stable"], DIMS.index(c["dimension"]) if c["dimension"] in DIMS else 9))
    return cands[: cfg["whatif"]["max_candidates"]]


# ---------------------------------------------------------------- vectors
def _mk(goal, sc, conf=None, n_sources=5, n_platforms=2, effort=100, hpw=10, weeks=12, team=2,
        risk=None, dup=None, objections=None, name=""):
    conf = conf or {d: 0.7 for d in DIMS}
    return {"name": name, "input": {
        "idea_id": "idea_t", "version": 1, "run_id": "run_t",
        "profile": {"goal": goal, "hours_per_week": hpw, "weeks_available": weeks, "team_size": team},
        "scores": [{"dimension": d, "score": sc[d], "confidence": conf[d], "evidence_ids": ["card_x#c0"] if sc[d] is not None else []} for d in DIMS],
        "gate_inputs": {"n_sources": n_sources, "n_platforms": n_platforms, "estimated_effort_hours": effort,
                        "risk_blocker": risk, "duplicate_of": dup},
        "objections_accepted": objections or []}}


def build_vectors() -> list[dict]:
    we = {"demand_pain": 3.5, "competition": 2.0, "differentiation": 3.0, "feasibility": 4.0, "risk": 3.5, "learning": 4.5}
    conf_we = {"demand_pain": 0.8, "competition": 0.6, "differentiation": 0.7, "feasibility": 0.8, "risk": 0.75, "learning": 0.85}
    good = {d: 4.0 for d in DIMS}
    v = [
        _mk("portfolio", we, conf_we, name="PDF worked example: 70.5 GO, BORDERLINE (feasibility weight -20% -> ~69.9 PIVOT)"),
        _mk("portfolio", {**we, "competition": 3.0}, conf_we, name="PDF worked example after what-if: competition 3.0 -> 73.5 stable GO"),
        _mk("startup", good, n_sources=2, name="evidence gate fail: 2 sources -> INSUFFICIENT_EVIDENCE"),
        _mk("startup", good, n_sources=3, n_platforms=1, name="evidence gate fail: 1 platform -> INSUFFICIENT_EVIDENCE"),
        _mk("startup", good, n_sources=3, n_platforms=2, name="evidence gate edge: exactly 3 sources / 2 platforms passes"),
        _mk("course_project", good, effort=30, hpw=20, weeks=1, team=1, name="feasibility gate fail: solo student 20 h, effort 30 h -> NO-GO"),
        _mk("startup", good, effort=30, hpw=200, weeks=1, team=1, name="same idea, team with 200 h -> passes feasibility"),
        _mk("startup", good, effort=240, hpw=10, weeks=12, team=2, name="feasibility edge: effort == available hours passes"),
        _mk("startup", good, risk="collects health data of minors without consent", name="risk gate fail -> NO-GO, blocker named"),
        _mk("startup", {**good, "differentiation": 2.5}, dup="card_notion01 (Notion)", name="duplicate gate fail (differentiation 2.5) -> NO-GO, product named"),
        _mk("startup", {**good, "differentiation": 3.0}, dup="card_notion01 (Notion)", name="duplicate gate edge: differentiation 3.0 overrides"),
        _mk("startup", {**good, "risk": None}, name="risk scorer abstained -> INSUFFICIENT_EVIDENCE (does not guess)"),
        _mk("startup", good, conf={d: 0.3 for d in DIMS}, n_sources=3, name="low confidence 0.39 -> INSUFFICIENT_EVIDENCE"),
        _mk("startup", good, conf={d: 0.5 for d in DIMS}, n_sources=5, name="confidence edge exactly 0.65 passes"),
        _mk("portfolio", {d: 3.5 for d in DIMS}, name="score exactly 70.0 -> GO (T_go inclusive)"),
        _mk("portfolio", {d: 2.5 for d in DIMS}, name="score exactly 50.0 -> PIVOT (T_no inclusive)"),
        _mk("portfolio", {d: 2.45 for d in DIMS}, name="score 49.0 -> NO-GO"),
        _mk("startup", {**good, "competition": 1.0}, name="floor breach (competition 1.0) with high score -> PIVOT, weakest = competition"),
        _mk("startup", {**good, "competition": 1.5}, name="floor edge: 1.5 is not a breach"),
        _mk("course_project", {**good, "learning": 0.5}, name="learning below floor does NOT breach (floor = first five only)"),
        _mk("startup", {d: 1.2 for d in DIMS}, name="floor breach + low score -> NO-GO (Figure 4: Score >= T_no? no)"),
        _mk("startup", {**good, "competition": 3.0}, objections=[{"dimension": "competition", "objection_id": "o1"}],
            name="challenge: one accepted objection -> competition -0.5, conf -0.1"),
        _mk("startup", {**good, "competition": 3.0}, objections=[{"dimension": "competition", "objection_id": f"o{i}"} for i in range(3)],
            name="challenge: three objections capped at -1.0 score"),
        _mk("course_project", {"demand_pain": 3.0, "competition": 3.0, "differentiation": 3.0, "feasibility": 3.0, "risk": 3.0, "learning": 3.0},
            name="PIVOT -> what-if proposes smallest change to GO"),
    ]
    for t in v:
        t["expected"] = evaluate(deepcopy(t["input"]))
    return v


if __name__ == "__main__":
    vecs = build_vectors()
    for t in vecs:
        e = t["expected"]
        print(f"- {t['name']}\n    -> {e['verdict']} {e['stability']} score={e['score']} conf={e['confidence']} "
              f"flipped={e['sensitivity']['flipped']}/{e['sensitivity']['runs']} reasons={e['reasons']}")
        for f in e["sensitivity"]["flips"]:
            print(f"       flip: {f}")
        for c in e.get("what_would_make_go", []):
            print(f"       whatif: {c}")
    out = Path(__file__).with_name("test_vectors.json")
    out.write_text(json.dumps(vecs, indent=2, sort_keys=True))
    print(f"\nwrote {out} ({len(vecs)} vectors)", file=sys.stderr)
