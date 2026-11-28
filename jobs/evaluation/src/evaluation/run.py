"""``python -m evaluation.run``: run the labelled set through the real Validate flow.

Writes ``<out>/<UTC timestamp>.json`` and prints the same document as the last stdout line
(``kubectl logs`` is how a cluster Job hands it out). Progress goes to stderr.

Exit codes: 0 results written and error rate within ``--max-error-rate``; 1 results written but
too many items errored (or nothing scored); 2 bad configuration (missing labelled set,
credentials or URL); 3 the gateway could not be reached or refused the eval user.
``baseline.json`` is written only by an explicit ``--write-baseline``, never automatically.
"""

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from evaluation.gateway import Gateway, GatewayError, Sleep
from evaluation.labelled import LabelledSetError, load_labelled
from evaluation.report import build_results
from evaluation.runner import RunConfig, Session, run_all, sign_in, slot_email

PROGRESS = "progress.jsonl"
BASELINE = "baseline.json"
SCHEMA = "eval-results@v1"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="evaluation.run", description=__doc__)
    p.add_argument("--labelled", type=Path, default=Path("eval/labelled/labelled_set.jsonl"))
    p.add_argument("--out", type=Path, default=Path("eval/results"))
    p.add_argument("--limit", type=int, default=None, help="first N items only")
    p.add_argument("--base-url", default=None, help="gateway URL (default $GATEWAY_URL)")
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--item-timeout", type=float, default=900.0)
    p.add_argument("--poll-interval", type=float, default=2.0)
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--max-cost-usd", type=float, default=0.05)
    p.add_argument("--max-error-rate", type=float, default=0.2)
    p.add_argument(
        "--resume", action="store_true", help="skip ids already scored in progress.jsonl"
    )
    p.add_argument(
        "--write-baseline",
        action="store_true",
        help="also write baseline.json (human-reviewed runs only)",
    )
    return p


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _load_progress(path: Path) -> dict[str, dict[str, Any]]:
    done: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return done
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn last line from a killed run: that item is simply run again
        if isinstance(row, dict) and row.get("status") == "ok" and isinstance(row.get("id"), str):
            done[row["id"]] = row
    return done


async def _execute(
    args: argparse.Namespace,
    env: Mapping[str, str],
    transport: httpx.AsyncBaseTransport | None,
    sleep: Sleep,
    now: Callable[[], datetime],
) -> int:
    base = args.base_url or env.get("GATEWAY_URL", "")
    user, password = env.get("EVAL_USER", ""), env.get("EVAL_PASSWORD", "")
    if not base or "@" not in user or not password:
        _say("config: need --base-url (or GATEWAY_URL), EVAL_USER (an email) and EVAL_PASSWORD")
        return 2
    if not 1 <= args.concurrency <= 8 or (args.limit is not None and args.limit < 1):
        _say("config: --concurrency must be 1..8 and --limit at least 1")
        return 2
    try:
        items = load_labelled(args.labelled)
    except LabelledSetError as exc:
        _say(f"labelled set unusable: {exc}")
        return 2
    if args.limit is not None:
        items = items[: args.limit]
    args.out.mkdir(parents=True, exist_ok=True)
    progress = args.out / PROGRESS
    previous = _load_progress(progress) if args.resume else {}
    if not args.resume:
        progress.write_text("")
    todo = [i for i in items if i.id not in previous]
    cfg = RunConfig(
        concurrency=args.concurrency,
        item_timeout_s=args.item_timeout,
        poll_s=args.poll_interval,
        item_retries=min(args.retries, 2),
        max_cost_usd=args.max_cost_usd,
        run_tag=now().strftime("%m%d%H%M%S"),
    )
    started = now()
    timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(base_url=base, timeout=timeout, transport=transport) as client:
        gateway = Gateway(client, retries=cfg.item_retries, sleep=sleep)
        sessions: list[Session] = []
        try:
            for slot in range(min(cfg.concurrency, max(1, len(todo)))):
                sessions.append(await sign_in(gateway, slot_email(user, slot), password))
        except GatewayError as exc:
            _say(f"cannot sign in the eval user: {exc}")
            return 3

        def record(row: dict[str, Any]) -> None:
            with progress.open("a") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
            _say(f"{row['id']}: {row['status']} {row.get('system') or row.get('error')}")

        fresh = (
            await run_all(todo, sessions, gateway, cfg, on_row=record, sleep=sleep) if todo else []
        )
    by_id = {r["id"]: r for r in [*previous.values(), *fresh]}
    rows = [by_id[i.id] for i in items if i.id in by_id]
    finished = now()
    meta = {
        "schema": SCHEMA,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_s": round((finished - started).total_seconds(), 1),
        "labelled_file": args.labelled.name,
        "limit": args.limit,
        "concurrency": cfg.concurrency,
        "resumed": len(previous),
    }
    results = build_results(rows, meta)
    document = json.dumps(results, indent=2) + "\n"
    target = args.out / f"{finished.strftime('%Y%m%dT%H%M%SZ')}.json"
    target.write_text(document)
    wrote_baseline = False
    if args.write_baseline and results["n_errors"] == 0 and results["n"] > 0:
        (args.out / BASELINE).write_text(document)
        wrote_baseline = True
    elif args.write_baseline:
        _say("baseline NOT written: it needs a run with no errored item")
    _say(f"wrote {target}" + (f" and {BASELINE}" if wrote_baseline else ""))
    print(json.dumps(results, separators=(",", ":")), flush=True)
    if results["n"] == 0 or results["error_rate"] > args.max_error_rate:
        return 1
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Sleep = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(
        _execute(args, env if env is not None else os.environ, transport, sleep, now)
    )


if __name__ == "__main__":
    raise SystemExit(main())
