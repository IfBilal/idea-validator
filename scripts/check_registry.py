#!/usr/bin/env python3
"""CI check for the prompt / rubric / config registry (docs/harness/12 section 4).

    python scripts/check_registry.py            # compare the tree with registry.lock.json
    python scripts/check_registry.py --update   # register NEW versions (never rewrites old ones)

Exit 0 consistent, 1 a rule is broken (edited in place, deleted, config changed without a version
bump, unregistered version, malformed tree), 2 bad usage or an unusable lock. Needs only PyYAML:
`uv run --no-project --with pyyaml python scripts/check_registry.py`.
"""

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def _registry() -> ModuleType:
    """libs/common/src/common/registry.py loaded by path: no install of the workspace needed."""
    path = ROOT / "libs" / "common" / "src" / "common" / "registry.py"
    spec = importlib.util.spec_from_file_location("_registry", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_registry"] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="repository root (default: this one)"
    )
    parser.add_argument("--update", action="store_true", help="add new versions to the lock")
    args = parser.parse_args(argv)
    reg = _registry()
    root: Path = args.root
    lock_path = root / reg.LOCK_NAME

    scanned = reg.scan(root)
    problems = list(scanned.problems)
    if problems:
        print("registry: the tree is malformed", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        return 1

    try:
        lock = reg.load_lock(lock_path) if lock_path.exists() else {}
    except ValueError as exc:
        print(f"registry: {exc}", file=sys.stderr)
        return 2
    if not lock_path.exists() and not args.update:
        print(
            f"registry: {reg.LOCK_NAME} is missing; run with --update to create it", file=sys.stderr
        )
        return 1

    found = reg.compare(lock, scanned.entries)
    broken = [p for p in found if not p.startswith("not registered")]

    if args.update:
        if broken:  # --update can add versions, it can never launder an edit or a deletion
            print("registry: refusing to update, existing versions were changed:", file=sys.stderr)
            for line in broken:
                print(f"  - {line}", file=sys.stderr)
            return 1
        merged = dict(lock)
        merged.update({k: e.sha256 for k, e in scanned.entries.items() if k not in lock})
        lock_path.write_text(reg.render_lock(merged))
        added = sorted(set(merged) - set(lock))
        print(
            f"registry: {len(added)} new entries registered" + "".join(f"\n  + {k}" for k in added)
        )
        return 0

    if found:
        print("registry: FAILED", file=sys.stderr)
        for line in found:
            print(f"  - {line}", file=sys.stderr)
        print(
            "A prompt or rubric change needs a NEW version file (name@v<N+1>), a config change needs "
            "its version key bumped; then run --update and re-run the evaluation.",
            file=sys.stderr,
        )
        return 1
    print(f"registry: ok, {len(scanned.entries)} entries match {reg.LOCK_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
