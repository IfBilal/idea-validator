"""Drift statistics: freshness, coverage and the verdict distribution shift (total variation)."""

import gzip
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentops import drift
from svc_agentops.models.drift import VERDICTS, DriftReport

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
TODAY = NOW.date()


def stats(by_source: dict[str, int], last: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "cards": sum(by_source.values()),
        "chunks": 0,
        "by_source": by_source,
        "by_kind": {},
        "last_ingested_at": last or {},
        "fresh_90d_share": 0.5,
        "snapshot_date": "2026-11-27",
    }


def write_snapshot(path: Path, cards: list[dict[str, Any]]) -> Path:
    with gzip.open(path, "wt") as fh:
        for c in cards:
            fh.write(json.dumps(c) + "\n")
    return path


# ---- total variation distance --------------------------------------------------------------------
def test_tvd_of_identical_distributions_is_zero() -> None:
    assert drift.tvd({"GO": 0.5, "NO-GO": 0.5}, {"GO": 0.5, "NO-GO": 0.5}) == 0


def test_tvd_of_disjoint_distributions_is_one() -> None:
    assert drift.tvd({"GO": 1.0}, {"NO-GO": 1.0}) == 1


def test_tvd_is_symmetric_and_known_value() -> None:
    a = {"GO": 0.6, "PIVOT": 0.4}
    b = {"GO": 0.2, "PIVOT": 0.3, "NO-GO": 0.5}
    assert drift.tvd(a, b) == drift.tvd(b, a) == pytest.approx(0.5)


# ---- verdict shift -------------------------------------------------------------------------------
def shift(recent: list[str], previous: list[str], **kw: Any) -> Any:
    kw = {"window": len(recent), "min_runs": 1, "threshold": 0.2} | kw
    return drift.verdict_shift(recent + previous, **kw)


def test_a_synthetic_shift_is_flagged() -> None:
    result = shift(["GO"] * 8 + ["PIVOT"] * 2, ["NO-GO"] * 8 + ["PIVOT"] * 2)
    assert result.tvd == pytest.approx(0.8)
    assert result.shifted and result.enough_data
    assert result.recent.counts["GO"] == 8 and result.previous.counts["NO-GO"] == 8
    assert result.recent.shares["GO"] == 0.8


def test_identical_windows_are_not_a_shift() -> None:
    result = shift(["GO", "NO-GO"] * 5, ["GO", "NO-GO"] * 5)
    assert result.tvd == 0 and not result.shifted


def test_exactly_the_threshold_is_not_a_shift_and_just_above_is() -> None:
    at = shift(["GO"] * 8 + ["PIVOT"] * 2, ["GO"] * 6 + ["PIVOT"] * 4)  # tvd 0.2
    assert at.tvd == pytest.approx(0.2) and not at.shifted
    above = shift(["GO"] * 9 + ["PIVOT"], ["GO"] * 6 + ["PIVOT"] * 4)  # tvd 0.3
    assert above.shifted


def test_too_few_runs_never_flag() -> None:
    result = drift.verdict_shift(
        ["GO", "GO", "NO-GO", "NO-GO"], window=2, min_runs=3, threshold=0.2
    )
    assert not result.enough_data and not result.shifted and result.tvd is None


def test_a_partial_previous_window_below_the_minimum_is_not_judged() -> None:
    result = drift.verdict_shift(["GO"] * 5 + ["NO-GO"] * 2, window=5, min_runs=5, threshold=0.2)
    assert result.previous.runs == 2 and not result.enough_data


def test_no_runs_is_empty_but_valid() -> None:
    result = drift.verdict_shift([], window=20, min_runs=5, threshold=0.2)
    assert result.recent.runs == 0 and result.tvd is None and not result.shifted
    assert set(result.recent.counts) == set(VERDICTS)


def test_unknown_verdict_labels_are_ignored_not_counted() -> None:
    result = drift.verdict_shift(["GO", "MAYBE", "GO"], window=3, min_runs=1, threshold=0.2)
    assert result.recent.runs == 2


def test_only_the_newest_window_and_the_one_before_are_used() -> None:
    newest_first = ["GO"] * 3 + ["NO-GO"] * 3 + ["PIVOT"] * 50
    result = drift.verdict_shift(newest_first, window=3, min_runs=1, threshold=0.2)
    assert result.previous.counts["PIVOT"] == 0 and result.previous.runs == 3


def test_window_must_be_positive() -> None:
    with pytest.raises(ValueError, match="window"):
        drift.verdict_shift([], window=0, min_runs=1, threshold=0.2)


# ---- verdicts from traces ------------------------------------------------------------------------
def decision(run: str, verdict: str, second: int, name: str = "m11_verdict") -> dict[str, Any]:
    return {
        "run_id": run,
        "kind": "decision",
        "ts": (NOW - timedelta(seconds=second)).isoformat(),
        "step_id": 1,
        "output": {"decision": name, "verdict": verdict},
    }


def test_verdicts_are_newest_first_one_per_run() -> None:
    runs = {
        "a": [decision("a", "GO", 300)],
        "b": [decision("b", "NO-GO", 100), decision("b", "PIVOT", 50)],  # a later what-if wins
        "c": [{"run_id": "c", "kind": "tool_call", "ts": NOW.isoformat(), "output": {}}],
        "d": [decision("d", "GO", 10, name="something_else")],
    }
    assert drift.extract_verdicts(runs.values()) == ["PIVOT", "GO"]


def test_malformed_records_are_skipped() -> None:
    bad: list[dict[str, Any]] = [
        {
            "kind": "decision",
            "ts": "not a date",
            "output": {"decision": "m11_verdict", "verdict": "GO"},
        },
        {"kind": "decision", "ts": NOW.isoformat(), "output": "str"},
        {"kind": "decision", "ts": NOW.isoformat(), "output": {"decision": "m11_verdict"}},
    ]
    assert drift.extract_verdicts([bad]) == []


# ---- freshness -----------------------------------------------------------------------------------
def test_freshness_from_stats_marks_old_sources_stale() -> None:
    fresh = drift.freshness(
        stats(
            {"hackernews": 10, "reddit": 5, "empty": 0},
            {"hackernews": "2026-11-27T01:00:00Z", "reddit": "2026-08-01T00:00:00Z"},
        ),
        None,
        TODAY,
        stale_after_days=30,
    )
    by = {s.source: s for s in fresh.sources}
    assert by["hackernews"].newest_age_days == 1 and not by["hackernews"].stale
    assert by["reddit"].stale and by["reddit"].newest_age_days == 119
    assert by["empty"].stale and by["empty"].newest_card is None and by["empty"].basis == "none"
    assert fresh.fresh_90d_share == 0.5


def test_age_exactly_at_the_limit_is_fresh_and_one_day_over_is_stale() -> None:
    last = {
        "edge": (TODAY - timedelta(days=30)).isoformat(),
        "over": (TODAY - timedelta(days=31)).isoformat(),
    }
    fresh = drift.freshness(stats({"edge": 1, "over": 1}, last), None, TODAY, stale_after_days=30)
    by = {s.source: s for s in fresh.sources}
    assert not by["edge"].stale and by["over"].stale


def test_a_card_dated_in_the_future_has_age_zero() -> None:
    last = {"x": (TODAY + timedelta(days=9)).isoformat()}
    fresh = drift.freshness(stats({"x": 1}, last), None, TODAY, stale_after_days=30)
    assert fresh.sources[0].newest_age_days == 0


def test_snapshot_published_dates_take_precedence_over_ingest_dates(tmp_path: Path) -> None:
    snap = drift.summarise_snapshot(
        write_snapshot(
            tmp_path / "c.jsonl.gz",
            [
                {"source": "github", "published_at": "2026-11-20", "fetched_at": "2026-11-27"},
                {"source": "github", "published_at": "2026-05-01", "fetched_at": "2026-11-27"},
                {"source": "reddit", "published_at": None, "fetched_at": "2026-10-01"},
            ],
        )
    )
    fresh = drift.freshness(stats({"github": 2}, {"github": "2026-11-27"}), snap, TODAY, 30)
    by = {s.source: s for s in fresh.sources}
    assert by["github"].newest_card == date(2026, 11, 20) and by["github"].basis == "published_at"
    assert by["reddit"].newest_card == date(2026, 10, 1) and by["reddit"].stale


def test_no_inputs_gives_an_empty_freshness() -> None:
    assert drift.freshness(None, None, TODAY, 30).sources == []


# ---- coverage ------------------------------------------------------------------------------------
def test_coverage_per_month_and_zero_new_sources_from_a_snapshot(tmp_path: Path) -> None:
    cards = (
        [{"source": "github", "published_at": "2026-11-10"}] * 3
        + [{"source": "github", "published_at": "2026-10-02"}]
        + [{"source": "reddit", "published_at": "2026-06-01"}]
        + [{"source": "lobsters", "published_at": None, "fetched_at": "2026-11-20"}]
        + [{"source": "junk", "published_at": "garbage", "fetched_at": None}]
    )
    snap = drift.summarise_snapshot(write_snapshot(tmp_path / "c.jsonl.gz", cards))
    cov = drift.coverage(None, snap, None, TODAY, window_days=30, months=6)
    assert cov.basis == "snapshot"
    assert cov.months["github"] == {"2026-10": 1, "2026-11": 3}
    assert "2026-06" in cov.months["reddit"]
    assert cov.new_cards["github"] == 3 and cov.new_cards["lobsters"] == 1
    assert cov.zero_new_sources == ["junk", "reddit"]


def test_months_older_than_the_horizon_are_cut(tmp_path: Path) -> None:
    cards = [
        {"source": "s", "published_at": "2025-01-01"},
        {"source": "s", "published_at": "2026-11-01"},
    ]
    snap = drift.summarise_snapshot(write_snapshot(tmp_path / "c.jsonl.gz", cards))
    cov = drift.coverage(None, snap, None, TODAY, window_days=30, months=6)
    assert cov.months["s"] == {"2026-11": 1}


def test_stats_delta_against_the_previous_report() -> None:
    previous = drift.build_report(
        now=NOW - timedelta(days=1), stats=stats({"a": 10, "b": 5}), snapshot=None, verdicts=[]
    )
    now_stats = stats({"a": 14, "b": 5, "c": 2})
    cov = drift.coverage(now_stats, None, previous, TODAY, window_days=30, months=6)
    assert cov.basis == "stats_delta"
    assert cov.new_cards == {"a": 4, "b": 0, "c": 2}
    assert cov.zero_new_sources == ["b"]


def test_a_shrinking_source_counts_as_zero_new_not_negative() -> None:
    previous = drift.build_report(now=NOW, stats=stats({"a": 10}), snapshot=None, verdicts=[])
    cov = drift.coverage(stats({"a": 7}), None, previous, TODAY, window_days=30, months=6)
    assert cov.new_cards == {"a": 0}


def test_stats_without_a_previous_report_cannot_judge_coverage() -> None:
    cov = drift.coverage(stats({"a": 10}), None, None, TODAY, window_days=30, months=6)
    assert cov.basis == "none" and cov.zero_new_sources == []


def test_empty_everything_coverage() -> None:
    cov = drift.coverage(None, None, None, TODAY, window_days=30, months=6)
    assert cov.basis == "none" and cov.months == {} and cov.new_cards == {}


# ---- the whole report ----------------------------------------------------------------------------
def test_report_flags_stale_silent_sources_and_a_shift() -> None:
    previous = drift.build_report(
        now=NOW, stats=stats({"a": 10, "b": 5}), snapshot=None, verdicts=[]
    )
    report = drift.build_report(
        now=NOW + timedelta(days=1),
        stats=stats({"a": 12, "b": 5}, {"a": "2026-11-28", "b": "2026-01-01"}),
        snapshot=None,
        verdicts=["GO"] * 6 + ["NO-GO"] * 6,
        previous=previous,
        window=6,
        min_runs=5,
    )
    assert report.flags == ["stale:b", "no_new_cards:b", "verdict_shift"]
    assert report.inputs.previous_report and report.inputs.stats and not report.inputs.snapshot


def test_a_report_with_no_inputs_is_valid_and_flags_missing_data() -> None:
    report = drift.build_report(now=NOW, stats=None, snapshot=None, verdicts=[])
    assert report.flags == ["no_kb_data"]
    assert not report.verdicts.enough_data
    assert DriftReport.model_validate_json(report.model_dump_json()) == report


def test_the_report_is_deterministic_for_the_same_inputs() -> None:
    args: dict[str, Any] = {
        "now": NOW,
        "stats": stats({"a": 1}),
        "snapshot": None,
        "verdicts": ["GO"],
    }
    assert (
        drift.build_report(**args).model_dump_json() == drift.build_report(**args).model_dump_json()
    )


def test_stress_a_large_snapshot_and_many_runs(tmp_path: Path) -> None:
    cards = [
        {"source": f"s{i % 7}", "published_at": (TODAY - timedelta(days=i % 400)).isoformat()}
        for i in range(30_000)
    ]
    snap = drift.summarise_snapshot(write_snapshot(tmp_path / "big.jsonl.gz", cards))
    report = drift.build_report(now=NOW, stats=None, snapshot=snap, verdicts=list(VERDICTS) * 5000)
    assert len(report.freshness.sources) == 7
    assert report.verdicts.recent.runs == 20 and not report.verdicts.shifted


def test_a_malformed_snapshot_line_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl.gz"
    with gzip.open(path, "wt") as fh:
        fh.write('{"source": "a", "published_at": "2026-11-01"}\nnot json\n[1]\n{"source": 5}\n')
    snap = drift.summarise_snapshot(path)
    assert snap.cards == {"a": 1}
