"""Drift report (docs/harness/12 section 6): ``python -m agentops.drift``.

Three questions, answered from data the system already has and written as one JSON file:

* freshness: how old is the newest card of every source (M6 ``/stats`` or a snapshot);
* coverage: cards per source and month, and which sources brought no new cards lately;
* verdict distribution: share of GO / PIVOT / NO-GO / INSUFFICIENT_EVIDENCE over the last N runs
  against the N before, compared by total variation distance (half the sum of absolute share
  differences, 0 = same, 1 = disjoint) and flagged above a threshold (default 0.2).

The verdicts are read from the ``m11_verdict`` decision traces, never recomputed. Exit codes: 0
report written (flags are reported, not failures), 1 flags present with ``--strict``, 2 bad usage
or no usable input.
"""

import argparse
import gzip
import json
import os
import sys
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from common.context import INTERNAL_TOKEN_HEADER
from svc_agentops.models.drift import (
    VERDICTS,
    Coverage,
    Distribution,
    DriftInputs,
    DriftReport,
    Freshness,
    SourceFreshness,
    VerdictShift,
)
from svc_agentops.repositories.files import JsonlTraceFiles

DECISION = "m11_verdict"
MAX_SNAPSHOT_LINES = 2_000_000
MAX_RUNS_SCANNED = 400
HTTP_TIMEOUT_S = 10.0


# ---- verdict distribution ------------------------------------------------------------------------
def tvd(p: Mapping[str, float], q: Mapping[str, float]) -> float:
    keys = set(p) | set(q)
    return 0.5 * sum(abs(p.get(k, 0.0) - q.get(k, 0.0)) for k in keys)


def _distribution(labels: list[str]) -> Distribution:
    counts = {v: labels.count(v) for v in VERDICTS}
    total = len(labels)
    return Distribution(
        runs=total,
        counts=counts,
        shares={v: (counts[v] / total if total else 0.0) for v in VERDICTS},
    )


def verdict_shift(
    newest_first: list[str], *, window: int, min_runs: int, threshold: float
) -> VerdictShift:
    if window < 1:
        raise ValueError("window must be at least 1")
    known = [v for v in newest_first if v in VERDICTS]
    recent = _distribution(known[:window])
    previous = _distribution(known[window : 2 * window])
    enough = recent.runs >= min_runs and previous.runs >= min_runs
    distance = round(tvd(recent.shares, previous.shares), 9) if enough else None
    return VerdictShift(
        window=window,
        min_runs=min_runs,
        recent=recent,
        previous=previous,
        tvd=distance,
        threshold=threshold,
        enough_data=enough,
        shifted=distance is not None and distance > threshold,
    )


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def extract_verdicts(runs: Iterable[list[dict[str, Any]]]) -> list[str]:
    """The verdict M11 gave each run (its latest ``m11_verdict`` step), newest run first."""
    found: list[tuple[datetime, str]] = []
    for records in runs:
        best: tuple[datetime, str] | None = None
        for rec in records:
            out = rec.get("output")
            ts = _parse_ts(rec.get("ts"))
            if (
                rec.get("kind") != "decision"
                or ts is None
                or not isinstance(out, dict)
                or out.get("decision") != DECISION
                or not isinstance(out.get("verdict"), str)
            ):
                continue
            if best is None or ts > best[0]:
                best = (ts, out["verdict"])
        if best:
            found.append(best)
    return [v for _, v in sorted(found, key=lambda f: f[0], reverse=True)]


# ---- KB side -------------------------------------------------------------------------------------
def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


@dataclass
class Snapshot:
    cards: dict[str, int] = field(default_factory=dict)
    published: dict[str, Counter[date]] = field(default_factory=dict)  # per source and day
    fetched: dict[str, Counter[date]] = field(default_factory=dict)  # cards without published_at


def summarise_snapshot(path: Path) -> Snapshot:
    snap = Snapshot()
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for n, line in enumerate(fh):
            if n >= MAX_SNAPSHOT_LINES:
                break
            try:
                card = json.loads(line)
            except ValueError:
                continue
            source = card.get("source") if isinstance(card, dict) else None
            if not isinstance(source, str) or not source:
                continue
            snap.cards[source] = snap.cards.get(source, 0) + 1
            if day := _as_date(card.get("published_at")):
                snap.published.setdefault(source, Counter())[day] += 1
            elif day := _as_date(card.get("fetched_at")):
                snap.fetched.setdefault(source, Counter())[day] += 1
    return snap


def _newest(snap: Snapshot | None, source: str) -> tuple[date | None, str]:
    if snap is not None:
        if days := snap.published.get(source):
            return max(days), "published_at"
        if days := snap.fetched.get(source):
            return max(days), "last_ingested_at"
    return None, "none"


def freshness(
    stats: Mapping[str, Any] | None, snap: Snapshot | None, today: date, stale_after_days: int
) -> Freshness:
    by_source: dict[str, int] = dict(stats.get("by_source", {})) if stats else {}
    last = stats.get("last_ingested_at", {}) if stats else {}
    names = sorted(set(by_source) | set(snap.cards if snap else {}))
    rows: list[SourceFreshness] = []
    for name in names:
        newest, basis = _newest(snap, name)
        if newest is None and (ingested := _as_date(last.get(name))):
            newest, basis = ingested, "last_ingested_at"
        age = max(0, (today - newest).days) if newest else None
        rows.append(
            SourceFreshness(
                source=name,
                cards=by_source.get(name, snap.cards.get(name) if snap else None),
                newest_card=newest,
                newest_age_days=age,
                basis=basis,
                stale=age is None or age > stale_after_days,
            )
        )
    share = stats.get("fresh_90d_share") if stats else None
    return Freshness(
        stale_after_days=stale_after_days,
        fresh_90d_share=float(share) if isinstance(share, int | float) else None,
        sources=rows,
    )


def _month_floor(today: date, months: int) -> str:
    index = today.year * 12 + today.month - 1 - (months - 1)
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def coverage(
    stats: Mapping[str, Any] | None,
    snap: Snapshot | None,
    previous: DriftReport | None,
    today: date,
    *,
    window_days: int,
    months: int,
) -> Coverage:
    if snap is not None:
        floor = _month_floor(today, months)
        since = today - timedelta(days=window_days)
        per_month: dict[str, dict[str, int]] = {}
        new_cards: dict[str, int] = {}
        for source in snap.cards:
            dated = snap.published.get(source, Counter()) + snap.fetched.get(source, Counter())
            monthly = Counter(f"{d.year:04d}-{d.month:02d}" for d in dated.elements())
            kept = {m: n for m, n in sorted(monthly.items()) if m >= floor}
            if kept:
                per_month[source] = kept
            new_cards[source] = sum(n for d, n in dated.items() if since <= d <= today)
        return Coverage(
            basis="snapshot",
            months=per_month,
            new_cards=new_cards,
            zero_new_sources=sorted(s for s, n in new_cards.items() if n == 0),
            window_days=window_days,
        )
    if stats is not None and previous is not None:
        before = {s.source: s.cards or 0 for s in previous.freshness.sources}
        now_counts: dict[str, int] = dict(stats.get("by_source", {}))
        delta = {s: max(0, n - before.get(s, 0)) for s, n in sorted(now_counts.items())}
        return Coverage(
            basis="stats_delta",
            months={},
            new_cards=delta,
            zero_new_sources=sorted(s for s, n in delta.items() if n == 0),
            window_days=window_days,
        )
    return Coverage(
        basis="none", months={}, new_cards={}, zero_new_sources=[], window_days=window_days
    )


# ---- the report ----------------------------------------------------------------------------------
def build_report(
    *,
    now: datetime,
    stats: Mapping[str, Any] | None,
    snapshot: Snapshot | None,
    verdicts: list[str],
    previous: DriftReport | None = None,
    window: int = 20,
    min_runs: int = 5,
    threshold: float = 0.2,
    stale_after_days: int = 30,
    window_days: int = 30,
    months: int = 6,
) -> DriftReport:
    today = now.date()
    fresh = freshness(stats, snapshot, today, stale_after_days)
    cover = coverage(stats, snapshot, previous, today, window_days=window_days, months=months)
    shift = verdict_shift(verdicts, window=window, min_runs=min_runs, threshold=threshold)
    flags = [f"stale:{s.source}" for s in fresh.sources if s.stale]
    flags += [f"no_new_cards:{s}" for s in cover.zero_new_sources]
    if stats is None and snapshot is None:
        flags.append("no_kb_data")
    if shift.shifted:
        flags.append("verdict_shift")
    return DriftReport(
        generated_at=now,
        inputs=DriftInputs(
            stats=stats is not None,
            snapshot=snapshot is not None,
            traces=bool(verdicts),
            previous_report=previous is not None,
        ),
        freshness=fresh,
        coverage=cover,
        verdicts=shift,
        flags=flags,
    )


# ---- inputs and CLI ------------------------------------------------------------------------------
def runs_from_dir(directory: Path, limit: int) -> Iterator[list[dict[str, Any]]]:
    files = JsonlTraceFiles(directory)
    paths = sorted(
        (p for p in directory.glob("*.jsonl") if p.is_file() and not p.is_symlink()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in paths[:limit]:
        rows, _ = files.read(path.stem, 100_000)
        yield [rec for _, rec in rows]


def runs_from_url(url: str, token: str | None, limit: int) -> Iterator[list[dict[str, Any]]]:
    headers = {INTERNAL_TOKEN_HEADER: token} if token else {}
    base = url.rstrip("/")
    seen, after = 0, None
    while seen < limit:
        params: dict[str, Any] = {"limit": min(100, limit - seen)}
        if after:
            params["after"] = after
        page = httpx.get(f"{base}/runs", params=params, headers=headers, timeout=HTTP_TIMEOUT_S)
        page.raise_for_status()
        body = page.json()
        for run in body["runs"]:
            seen += 1
            trace = httpx.get(
                f"{base}/runs/{run['run_id']}/trace",
                params={"flat": "true"},
                headers=headers,
                timeout=HTTP_TIMEOUT_S,
            )
            if trace.status_code == 404:
                continue
            trace.raise_for_status()
            yield [r for r in trace.json()["records"] if isinstance(r, dict)]
        after = body.get("next")
        if not after:
            return


def _get_json(url: str, token: str | None) -> Any:
    headers = {INTERNAL_TOKEN_HEADER: token} if token else {}
    response = httpx.get(url, headers=headers, timeout=HTTP_TIMEOUT_S)
    response.raise_for_status()
    return response.json()


def _previous(args: argparse.Namespace, token: str | None) -> DriftReport | None:
    try:
        if args.out_dir and (files := sorted(Path(args.out_dir).glob("drift-*.json"))):
            return DriftReport.model_validate_json(files[-1].read_text())
        if args.url and args.post:
            response = httpx.get(
                f"{args.url.rstrip('/')}/drift/latest",
                headers={INTERNAL_TOKEN_HEADER: token} if token else {},
                timeout=HTTP_TIMEOUT_S,
            )
            if response.status_code == 200:
                return DriftReport.model_validate(response.json())
    except (OSError, ValueError, httpx.HTTPError):
        return None  # no usable earlier report: coverage falls back to "none"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentops.drift", description=__doc__)
    parser.add_argument("--trace-dir", type=Path, default=os.environ.get("TRACE_DIR"))
    parser.add_argument("--url", default=os.environ.get("AGENTOPS_URL"), help="M13 base URL")
    parser.add_argument("--kb-url", default=os.environ.get("KB_URL"), help="M6 base URL (/stats)")
    parser.add_argument("--stats-file", type=Path, help="a saved M6 /stats answer")
    parser.add_argument("--snapshot", type=Path, help="cards-<date>.jsonl.gz")
    parser.add_argument("--out-dir", type=Path, default=os.environ.get("DRIFT_DIR"))
    parser.add_argument("--post", action="store_true", help="POST the report to M13 /drift")
    parser.add_argument("--window", type=int, default=20)
    parser.add_argument("--min-runs", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=0.2)
    parser.add_argument("--stale-days", type=int, default=30)
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--strict", action="store_true", help="exit 1 when any flag is raised")
    args = parser.parse_args(argv)
    token = os.environ.get("INTERNAL_TOKEN")
    if args.window < 1 or not 0 <= args.threshold <= 1:
        print("--window must be >= 1 and --threshold within 0..1", file=sys.stderr)
        return 2
    try:
        stats: dict[str, Any] | None = None
        if args.stats_file:
            stats = json.loads(args.stats_file.read_text())
        elif args.kb_url:
            stats = _get_json(f"{args.kb_url.rstrip('/')}/stats", token)
        snapshot = summarise_snapshot(args.snapshot) if args.snapshot else None
        runs: Iterable[list[dict[str, Any]]] = []
        if args.trace_dir:
            runs = runs_from_dir(Path(args.trace_dir), MAX_RUNS_SCANNED)
        elif args.url:
            runs = runs_from_url(args.url, token, MAX_RUNS_SCANNED)
        verdicts = extract_verdicts(runs)
        if stats is None and snapshot is None and not verdicts:
            print(
                "no usable input: give --kb-url/--stats-file/--snapshot or traces", file=sys.stderr
            )
            return 2
        report = build_report(
            now=datetime.now(UTC),
            stats=stats,
            snapshot=snapshot,
            verdicts=verdicts,
            previous=_previous(args, token),
            window=args.window,
            min_runs=args.min_runs,
            threshold=args.threshold,
            stale_after_days=args.stale_days,
            window_days=args.window_days,
        )
        text = report.model_dump_json(indent=2)
        if args.out_dir:
            Path(args.out_dir).mkdir(parents=True, exist_ok=True)
            stamp = report.generated_at.strftime("%Y%m%dT%H%M%SZ")
            (Path(args.out_dir) / f"drift-{stamp}.json").write_text(text)
        if args.post:
            if not args.url:
                print("--post needs --url (or AGENTOPS_URL)", file=sys.stderr)
                return 2
            headers = {INTERNAL_TOKEN_HEADER: token} if token else {}
            httpx.post(
                f"{args.url.rstrip('/')}/drift",
                content=text,
                headers={**headers, "Content-Type": "application/json"},
                timeout=HTTP_TIMEOUT_S,
            ).raise_for_status()
    except (OSError, ValueError, KeyError, httpx.HTTPError) as exc:
        print(f"drift failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"flags": report.flags, "generated_at": report.generated_at.isoformat()}))
    return 1 if args.strict and report.flags else 0


if __name__ == "__main__":
    sys.exit(main())
