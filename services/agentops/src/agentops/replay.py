"""Replay a recorded run (docs/harness/12 §2).

``python -m agentops.replay <run_id> [--trace-dir D | --url U] [--config-dir C]``.

Recorded mode re-runs the pure M11 engine in-process on the engine inputs the orchestrator traced
(decision ``m11_verdict``) and compares the result with the verdict M11 returned at the time. No
network, no model, no cost. Exit codes: 0 reproduced, 1 mismatch, 2 nothing replayable or bad usage.
"""

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from svc_verdict.models.inputs import EngineInput
from svc_verdict.services.engine_config import EngineConfig, load_engine_config
from svc_verdict.services.whatif import evaluate_with_whatif

from common.config import ConfigError
from common.context import INTERNAL_TOKEN_HEADER
from svc_agentops.repositories.files import JsonlTraceFiles, UnsafeRunId
from svc_agentops.services.sanitize import RUN_ID, TRUSTED_SERVICE

DECISION = "m11_verdict"
COMPARED = ("verdict", "stability", "score", "confidence", "gates")
MAX_RECORDS = 100_000


@dataclass
class StepReport:
    step: int
    ok: bool
    diffs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class ReplayReport:
    run_id: str
    steps: list[StepReport]
    problem: str | None = None
    m11_steps: int = 0  # recorded M11 verdict steps found (from the orchestrator only)

    @property
    def exit_code(self) -> int:
        if self.problem or not self.steps:
            return 2
        return 0 if all(s.ok for s in self.steps) else 1


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, int | float) and isinstance(b, int | float) and not isinstance(a, bool):
        return math.isclose(a, b, rel_tol=0, abs_tol=1e-9)
    return bool(a == b)


def replay_records(
    run_id: str,
    records: list[dict[str, Any]],
    rules: EngineConfig,
    *,
    allow_version_drift: bool = False,
    complete: bool = True,
) -> ReplayReport:
    """``complete=False`` means the stored trace was cut short: the proof would be partial."""
    if not complete:
        return ReplayReport(
            run_id, [], "the trace was truncated (too many records); not replayable"
        )
    found = [
        r
        for r in records
        if r.get("kind") == "decision"
        and r.get("service") == TRUSTED_SERVICE
        and isinstance(r.get("output"), dict)
        and r["output"].get("decision") == DECISION
    ]
    if not found:
        return ReplayReport(run_id, [], "no recorded M11 verdict step in this run")
    n_found = len(found)
    steps: list[StepReport] = []
    for n, rec in enumerate(sorted(found, key=lambda r: (r["ts"], r["step_id"])), start=1):
        raw = rec.get("input")
        if not isinstance(raw, dict) or raw.get("_truncated"):
            return ReplayReport(run_id, steps, f"step {n}: the recorded M11 input was truncated")
        try:
            result = evaluate_with_whatif(EngineInput.model_validate(raw), rules)
        except ValueError as exc:  # pydantic's ValidationError is a ValueError
            steps.append(StepReport(n, False, [f"recorded input is not valid engine input: {exc}"]))
            continue
        recorded = rec["output"]
        computed = result.model_dump(mode="json")
        diffs = [
            f"{key}: recorded {recorded.get(key)!r}, replayed {computed.get(key)!r}"
            for key in COMPARED
            if not _same(recorded.get(key), computed.get(key))
        ]
        notes = []
        if recorded.get("versions") != rules.versions:
            message = (
                f"rules versions differ: recorded {recorded.get('versions')}, now {rules.versions}"
            )
            if allow_version_drift:
                notes.append(message)
            else:
                diffs.append(message + " (use --allow-version-drift to accept)")
        steps.append(StepReport(n, not diffs, diffs, notes))
    return ReplayReport(run_id, steps, m11_steps=n_found)


def format_report(report: ReplayReport) -> str:
    lines = [f"replay of {report.run_id}: {report.m11_steps} recorded M11 verdict step(s)"]
    if report.problem:
        lines.append(f"cannot replay: {report.problem}")
    for s in report.steps:
        lines.append(f"  verdict step {s.step}: {'REPRODUCED' if s.ok else 'MISMATCH'}")
        lines += [f"    - {d}" for d in s.diffs]
        lines += [f"    note: {n}" for n in s.notes]
    if not report.problem:
        lines.append("result: same verdict" if report.exit_code == 0 else "result: DIFFERENT")
    return "\n".join(lines)


def load_from_dir(run_id: str, directory: Path) -> tuple[list[dict[str, Any]], bool]:
    """The run's records and whether they are all there (False when MAX_RECORDS cut them)."""
    rows, truncated = JsonlTraceFiles(directory).read(run_id, MAX_RECORDS)
    return [rec for _, rec in rows], not truncated


def load_from_url(run_id: str, url: str, token: str | None) -> tuple[list[dict[str, Any]], bool]:
    if not RUN_ID.fullmatch(run_id):
        raise UnsafeRunId("run id must match [A-Za-z0-9_-]{1,64}")
    headers = {INTERNAL_TOKEN_HEADER: token} if token else {}
    response = httpx.get(
        f"{url.rstrip('/')}/runs/{run_id}/trace",
        params={"flat": "true"},
        headers=headers,
        timeout=10,
    )
    response.raise_for_status()
    body = response.json()
    return [r for r in body["records"] if isinstance(r, dict)], not body.get("truncated", False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentops.replay", description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--mode", choices=["recorded"], default="recorded")
    parser.add_argument("--trace-dir", type=Path, default=os.environ.get("TRACE_DIR"))
    parser.add_argument("--url", default=os.environ.get("AGENTOPS_URL"))
    parser.add_argument("--config-dir", type=Path, default=os.environ.get("CONFIG_DIR"))
    parser.add_argument(
        "--allow-version-drift",
        action="store_true",
        help="accept that the rules (config/verdict.yaml) changed since the run",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    try:
        rules = load_engine_config(args.config_dir)
        if args.trace_dir:
            records, complete = load_from_dir(args.run_id, Path(args.trace_dir))
        elif args.url:
            records, complete = load_from_url(
                args.run_id, args.url, os.environ.get("INTERNAL_TOKEN")
            )
        else:
            print("give --trace-dir (or TRACE_DIR) or --url (or AGENTOPS_URL)", file=sys.stderr)
            return 2
    except (ConfigError, UnsafeRunId, httpx.HTTPError, OSError) as exc:
        print(f"replay failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    report = replay_records(
        args.run_id,
        records,
        rules,
        allow_version_drift=args.allow_version_drift,
        complete=complete,
    )
    if args.json:
        print(
            json.dumps({"exit_code": report.exit_code, "steps": [s.__dict__ for s in report.steps]})
        )
    else:
        print(format_report(report))
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
