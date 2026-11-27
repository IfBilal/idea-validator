"""CLI: ``gate BASELINE CURRENT`` (nightly gate) and ``tune CASES [CONFIG]`` (proposal)."""

import json
import sys
from pathlib import Path

import yaml
from svc_verdict.services.engine_config import parse_engine_config

from evaluation.gate import check_results
from evaluation.tuning import load_cases, propose


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "gate":
        failures = check_results(
            json.loads(Path(argv[1]).read_text()), json.loads(Path(argv[2]).read_text())
        )
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        print("evaluation gate: " + ("FAILED" if failures else "passed"))
        return 1 if failures else 0
    if len(argv) >= 2 and argv[0] == "tune":
        config_path = Path(argv[2]) if len(argv) > 2 else Path("config/verdict.yaml")
        cfg = parse_engine_config(yaml.safe_load(config_path.read_text()))
        print(json.dumps(propose(load_cases(Path(argv[1])), cfg), indent=2))
        return 0
    print(
        "usage: gate BASELINE.json CURRENT.json | tune CASES.jsonl [verdict.yaml]", file=sys.stderr
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
