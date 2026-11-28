"""``python -m evaluation.run``: run the labelled set through the real Validate flow.

Writes ``<out>/<UTC timestamp>.json`` (never over an existing file) and prints a marker line and
then the same document as the last stdout line (``kubectl logs`` is how a cluster Job hands it
out). Progress goes to stderr.

Exit codes: 0 results written and error rate within ``--max-error-rate``; 1 results written but
too many items errored (or nothing scored); 2 bad configuration (missing labelled set,
credentials or URL, or a refused ``--resume`` / ``--write-baseline``); 3 the gateway could not be
reached or refused the eval user. ``baseline.json`` is written only by an explicit
``--write-baseline`` on a complete, clean run, never automatically.
"""

import argparse
import asyncio
import json
import math
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from evaluation import progress
from evaluation.gateway import Gateway, GatewayError, Sleep
from evaluation.labelled import LabelledSetError, load_labelled
from evaluation.report import build_results
from evaluation.runner import RunConfig, Session, run_all, sign_in, slot_email

PROGRESS = "progress.jsonl"
BASELINE = "baseline.json"
SCHEMA = "eval-results@v1"
RESULT_MARKER = "-----EVAL-RESULT-----"  # the line before the result document in the log


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="evaluation.run", description=__doc__)
    p.add_argument("--labelled", type=Path, default=Path("eval/labelled/labelled_set.jsonl"))
    p.add_argument("--out", type=Path, default=Path("eval/results"))
    p.add_argument("--limit", type=int, default=None, help="first N items only")
    p.add_argument("--base-url", default=None, help="gateway URL (default $GATEWAY_URL)")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--item-timeout", type=float, default=900.0)
    p.add_argument(
        "--poll-interval",
        type=float,
        default=4.0,
        help="seconds between polls per worker; keep concurrency / interval under ~1 request "
        "per second in total (the gateway limits an IP to 100 requests a minute)",
    )
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--max-cost-usd", type=float, default=0.05, help="per item")
    p.add_argument(
        "--max-total-cost-usd",
        type=float,
        default=None,
        help="stop starting items once the finished ones cost this much",
    )
    p.add_argument("--max-error-rate", type=float, default=0.2)
    p.add_argument(
        "--resume", action="store_true", help="skip ids already scored in progress.jsonl"
    )
    p.add_argument(
        "--write-baseline",
        action="store_true",
        help="also write baseline.json (a complete, clean, human-reviewed run only)",
    )
    return p


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def baseline_refusal(results: Mapping[str, Any]) -> str | None:
    """Why this run may not become the baseline, or ``None``. Pre-run flag checks are separate."""
    if results["resumed"] > 0:
        return "it was resumed"
    if results["limit"] is not None:
        return "--limit was set"
    if results["n_errors"] > 0 or results["n"] == 0:
        return "some items errored or none was scored"
    if results["traces_available"] < results["n"]:
        return "not every run had a trace"
    for name, rate in results["behaviour"].items():
        if rate < 1.0:
            return f"behaviour check {name!r} is below 100%"
        if results["behaviour_applicable"].get(name, 0) == 0:
            return f"behaviour check {name!r} applied to no item"
    return None


def _config_problem(args: argparse.Namespace, env: Mapping[str, str]) -> str | None:
    if not (args.base_url or env.get("GATEWAY_URL")):
        return "need --base-url (or GATEWAY_URL)"
    if "@" not in env.get("EVAL_USER", "") or not env.get("EVAL_PASSWORD"):
        return "need EVAL_USER (an email) and EVAL_PASSWORD"
    if not 1 <= args.concurrency <= 8:
        return "--concurrency must be 1..8"
    if args.limit is not None and args.limit < 1:
        return "--limit must be at least 1"
    for name in ("item_timeout", "poll_interval", "max_cost_usd"):
        value = getattr(args, name)
        if not math.isfinite(value) or value < 0 or (name != "poll_interval" and value == 0):
            return f"--{name.replace('_', '-')} must be a positive number"
    if args.max_total_cost_usd is not None and not 0 < args.max_total_cost_usd < math.inf:
        return "--max-total-cost-usd must be a positive number"
    if args.write_baseline and (args.limit is not None or args.resume):
        return "--write-baseline needs a full run: not with --limit or --resume"
    return None


async def _execute(
    args: argparse.Namespace,
    env: Mapping[str, str],
    transport: httpx.AsyncBaseTransport | None,
    sleep: Sleep,
    now: Callable[[], datetime],
    clock: Callable[[], float],
) -> int:
    problem = _config_problem(args, env)
    if problem:
        _say(f"config: {problem}")
        return 2
    base = args.base_url or env["GATEWAY_URL"]
    try:
        items = load_labelled(args.labelled)
    except LabelledSetError as exc:
        _say(f"labelled set unusable: {exc}")
        return 2
    header = progress.make_header(SCHEMA, args.labelled, env.get("EVAL_IMAGE_TAG", ""))
    if args.limit is not None:
        items = items[: args.limit]
    args.out.mkdir(parents=True, exist_ok=True)
    log = args.out / PROGRESS
    previous: dict[str, dict[str, Any]] = {}
    if args.resume:
        try:
            previous, dropped = progress.load(log, header, items)
        except progress.ProgressError as exc:
            _say(f"resume refused: {exc}")
            return 2
        if dropped:
            _say(f"resume: {dropped} unusable progress line(s) ignored; those items run again")
    else:
        progress.start(log, header)
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
    user, password = env["EVAL_USER"], env["EVAL_PASSWORD"]
    async with httpx.AsyncClient(
        base_url=base, timeout=httpx.Timeout(30.0), transport=transport
    ) as client:
        gateway = Gateway(client, retries=cfg.item_retries, sleep=sleep)
        sessions: list[Session] = []
        try:
            for slot in range(min(cfg.concurrency, max(1, len(todo)))):
                sessions.append(await sign_in(gateway, slot_email(user, slot), password))
        except GatewayError as exc:
            _say(f"cannot sign in the eval user: {exc}")
            return 3

        def record(row: dict[str, Any]) -> None:
            progress.append(log, row)
            _say(f"{row['id']}: {row['status']} {row.get('system') or row.get('error')}")

        fresh = (
            await run_all(
                todo,
                sessions,
                gateway,
                cfg,
                on_row=record,
                sleep=sleep,
                max_total_cost_usd=args.max_total_cost_usd,
                clock=clock,
            )
            if todo
            else []
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
        "labelled_sha256": header["labelled_sha256"],
        "image_tag": header["image_tag"],
        "limit": args.limit,
        "concurrency": cfg.concurrency,
        "resumed": len(previous),
    }
    results = build_results(rows, meta)
    document = json.dumps(results, indent=2) + "\n"
    target = args.out / f"{finished.strftime('%Y%m%dT%H%M%SZ')}.json"
    code = 0
    try:
        progress.atomic_write(target, document, exclusive=True)
        _say(f"wrote {target}")
    except FileExistsError:
        _say(f"{target} already exists: not overwritten (the result is in the log)")
        code = 1
    if args.write_baseline:
        why = baseline_refusal(results)
        if why is None:
            progress.atomic_write(args.out / BASELINE, document)
            _say(f"wrote {args.out / BASELINE}")
        else:
            _say(f"baseline NOT written: {why}")
            code = 2
    print(RESULT_MARKER, flush=True)
    print(json.dumps(results, separators=(",", ":")), flush=True)
    if code == 0 and (results["n"] == 0 or results["error_rate"] > args.max_error_rate):
        return 1
    return code


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Sleep = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    clock: Callable[[], float] = time.monotonic,
) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(
        _execute(args, env if env is not None else os.environ, transport, sleep, now, clock)
    )


if __name__ == "__main__":
    raise SystemExit(main())
