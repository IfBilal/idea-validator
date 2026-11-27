"""Regression tests for the security review of PR 69 (ReDoS, line splitting, bombs, trust)."""

import gzip
import hashlib
import json
import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from ingest_fakes import FakeKb
from synthetic import synthetic_items, write_jsonl

from ingestion import lines as lines_mod
from ingestion.cli import main
from ingestion.dedupe import DedupeConfig, dedupe
from ingestion.mappers import Rejected, map_item
from ingestion.models import RawItem
from ingestion.pipeline import run_ingestion
from ingestion.snapshot import SnapshotError, restore_snapshot, write_snapshot
from ingestion.sources import JsonlSource
from ingestion.state import Cursor, StateStore

TODAY = date(2026, 3, 1)
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_TOKEN", "i")


def item(**kw: Any) -> RawItem:
    base = {"source": "hackernews", "url": "https://example.com/p", "title": "T", "text": "body"}
    return RawItem.model_validate(base | kw)


# ---- HIGH 1: no quadratic regex on hostile text ---------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "a" * 400_000,
        "." * 400_000,
        "a." * 200_000,
        "a" * 400_000 + "@",
        "@" * 400_000,
        "a@" * 200_000,
        "a" * 100_000 + "@" + "b" * 100_000,
        "u/" * 200_000,
        "-" * 400_000 + "@x",
    ],
)
def test_hostile_text_maps_in_well_under_a_second(text: str) -> None:
    start = time.perf_counter()
    map_item(item(text=text), fetched_on=TODAY)
    map_item(item(title=text[:5000], text="x"), fetched_on=TODAY)
    assert time.perf_counter() - start < 1.0


def test_emails_and_handles_are_still_masked() -> None:
    card = map_item(
        item(text="mail jane.doe+x@mail.example.co.uk now, ping @janedoe and u/bob_1, /user/zed"),
        fetched_on=TODAY,
    )
    for secret in ("jane.doe", "mail.example", "janedoe", "bob_1", "zed"):
        assert secret not in card.text


def test_an_at_handle_is_masked_but_the_email_domain_is_not_double_masked() -> None:
    card = map_item(item(text="a@b.io and @someone"), fetched_on=TODAY)
    assert card.text.count("[email removed]") == 1
    assert "someone" not in card.text


@pytest.mark.parametrize(
    "url",
    [
        "https://reddit.com/user/someone",
        "https://www.reddit.com/u/someone/",
        "https://old.reddit.com/user/x/comments/1",
    ],
)
def test_profile_urls_are_rejected(url: str) -> None:
    with pytest.raises(Rejected, match="profile"):
        map_item(item(url=url), fetched_on=TODAY)


def test_a_bad_url_is_reported_without_its_query_or_path() -> None:
    with pytest.raises(Rejected) as err:
        map_item(item(url="ftp://secret.example.com/private/path?token=SECRET"), fetched_on=TODAY)
    blob = err.value.reason + err.value.url
    assert "SECRET" not in blob and "private" not in blob and "token" not in blob


# ---- HIGH 2: snapshot round trip with Unicode line separators -----------------------------


SEPARATORS = [chr(0x2028), chr(0x2029), "\x85", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e"]


def rich_card(i: int, text: str) -> dict[str, Any]:
    from ingestion.mappers import content_hash_of

    return {
        "name": f"card {i}",
        "summary": f"summary {i}",
        "problem": None,
        "customer": None,
        "niche_tags": [],
        "stage": "unknown",
        "funding_usd": None,
        "source": "github",
        "kind": "repo",
        "url": f"https://x{i}.example.com/p",
        "published_at": None,
        "fetched_at": "2026-03-01",
        "text": text,
        "content_hash": content_hash_of(text),
    }


async def test_cards_with_line_separator_characters_survive_snapshot_and_restore(
    tmp_path: Path,
) -> None:
    kb = FakeKb()
    texts = [f"before{sep}after {i}" for i, sep in enumerate(SEPARATORS)]
    await kb.ingest([rich_card(i, t) for i, t in enumerate(texts)])
    manifest = await write_snapshot(kb, tmp_path, day=TODAY)
    assert manifest.total == len(SEPARATORS)
    fresh = FakeKb()
    report = await restore_snapshot(fresh, tmp_path / "cards-20260301.jsonl.gz")
    assert (report.added, report.dropped) == (len(SEPARATORS), 0)
    assert len(fresh.cards) == len(SEPARATORS)
    for i in range(len(SEPARATORS)):
        stored = fresh.cards[f"https://x{i}.example.com/p"]["text"]
        assert stored.startswith("before") and stored.endswith(f"after {i}")


def test_the_snapshot_file_has_exactly_one_line_per_card_even_with_separators() -> None:
    row = json.dumps({"text": "a" + chr(0x2028) + "b\x85c"}, ensure_ascii=True)
    assert chr(0x2028) not in row and "\x85" not in row


# ---- MED: parsing limits -------------------------------------------------------------------


def test_deeply_nested_json_is_a_rejected_row_not_a_crash(tmp_path: Path) -> None:
    path = tmp_path / "deep.jsonl"
    path.write_text(
        "[" * 200_000 + "]" * 200_000 + "\n" + json.dumps(next(synthetic_items(1))) + "\n"
    )
    rows = list(JsonlSource(path).read())
    assert len(rows) == 2 and type(rows[0]).__name__ == "Rejection"


def test_an_oversized_line_is_one_rejection_and_the_next_line_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lines_mod, "MAX_LINE_BYTES", 1000)
    good = json.dumps({"source": "github", "url": "https://a.com/x", "title": "t"})
    path = tmp_path / "big.jsonl"
    path.write_text(good + "\n" + "x" * 5000 + "\n" + good + "\n")
    kinds = [type(r).__name__ for r in JsonlSource(path).read()]
    assert kinds == ["RawItem", "Rejection", "RawItem"]


async def test_a_gzip_bomb_is_refused_with_a_snapshot_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lines_mod, "MAX_TOTAL_BYTES", 10_000)
    bomb = tmp_path / "cards-20260301.jsonl.gz"
    bomb.write_bytes(gzip.compress(b'{"a": 1}\n' * 100_000))
    with pytest.raises(SnapshotError, match="too large"):
        await restore_snapshot(FakeKb(), bomb, allow_unverified=True)


def test_the_cli_exits_three_on_a_bomb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KB_URL", "http://kb:8000")
    monkeypatch.setattr(lines_mod, "MAX_TOTAL_BYTES", 10_000)
    bomb = tmp_path / "cards-20260301.jsonl.gz"
    bomb.write_bytes(gzip.compress(b'{"a": 1}\n' * 100_000))
    assert main(["restore", str(bomb), "--allow-unverified"], kb_factory=lambda s: FakeKb()) == 3


def test_cursor_and_manifest_with_deep_json_are_ignored_or_refused(tmp_path: Path) -> None:
    (tmp_path / "cursor.json").write_text("[" * 200_000)
    assert StateStore(tmp_path).load() is None


# ---- MED: the manifest is mandatory and checked -------------------------------------------


async def make_snapshot(tmp_path: Path) -> Path:
    kb = FakeKb()
    await kb.ingest([rich_card(i, f"text {i}") for i in range(5)])
    await write_snapshot(kb, tmp_path, day=TODAY)
    return tmp_path / "cards-20260301.jsonl.gz"


async def test_restore_without_a_manifest_is_refused_unless_allowed(tmp_path: Path) -> None:
    path = await make_snapshot(tmp_path)
    (tmp_path / "cards-20260301.manifest.json").unlink()
    with pytest.raises(SnapshotError, match="manifest"):
        await restore_snapshot(FakeKb(), path)
    report = await restore_snapshot(FakeKb(), path, allow_unverified=True)
    assert report.added == 5


async def test_a_manifest_for_another_file_or_other_counts_is_refused(tmp_path: Path) -> None:
    path = await make_snapshot(tmp_path)
    manifest_path = tmp_path / "cards-20260301.manifest.json"
    original = json.loads(manifest_path.read_text())
    for change in ({"file": "cards-19990101.jsonl.gz"}, {"total": 4}, {"by_source": {"github": 1}}):
        manifest_path.write_text(json.dumps(original | change))
        with pytest.raises(SnapshotError):
            await restore_snapshot(FakeKb(), path)
    manifest_path.write_text(json.dumps(original))
    assert (await restore_snapshot(FakeKb(), path)).added == 5


async def test_restore_scrubs_text_and_recomputes_the_hash(tmp_path: Path) -> None:
    from ingestion.mappers import content_hash_of

    dirty = rich_card(1, "contact me at leak@example.com or u/leaker")
    dirty["url"] = "https://WWW.Reddit.com/r/x/comments/1/?utm_source=a"
    dirty["source"] = "reddit"
    path = tmp_path / "cards-20260301.jsonl.gz"
    blob = gzip.compress(
        (json.dumps(dirty | {"id": "card_00000000", "platforms": ["reddit"]}) + "\n").encode()
    )
    path.write_bytes(blob)
    (tmp_path / "cards-20260301.manifest.json").write_text(
        json.dumps(
            {
                "file": path.name,
                "sha256": hashlib.sha256(blob).hexdigest(),
                "total": 1,
                "by_source": {"reddit": 1},
            }
        )
    )
    fresh = FakeKb()
    report = await restore_snapshot(fresh, path)
    (card,) = fresh.calls[0]
    assert "leak@example.com" not in card["text"] and "leaker" not in card["text"]
    assert card["url"] == "https://reddit.com/r/x/comments/1"
    assert card["content_hash"] == content_hash_of(card["text"])
    assert report.id_mismatches == 1  # the made-up id above is not what M6 mints


def test_restore_exits_four_when_rows_were_dropped_or_ids_differ(tmp_path: Path) -> None:
    path = tmp_path / "cards-20260301.jsonl.gz"
    path.write_bytes(gzip.compress(b"{broken\n"))
    code = main(["restore", str(path), "--allow-unverified"], kb_factory=lambda s: FakeKb())
    assert code in (3, 4)


# ---- LOW ------------------------------------------------------------------------------------


def test_dedupe_order_includes_fetched_at_and_name() -> None:
    def card(name: str, fetched: str) -> Any:
        return map_item(
            item(url="https://a.com/same", title=name, text="x", fetched_at=fetched),
            fetched_on=TODAY,
        )

    a, b = card("Alpha one", "2026-02-01"), card("Beta two", "2026-02-02")
    one = dedupe([a, b], DedupeConfig()).cards[0].card
    two = dedupe([b, a], DedupeConfig()).cards[0].card
    assert one == two


def test_short_texts_are_not_near_duplicate_merged() -> None:
    a = map_item(item(url="https://a.com/1", title="Todo app", text=""), fetched_on=TODAY)
    b = map_item(item(url="https://b.com/2", title="Todo apps", text=""), fetched_on=TODAY)
    assert len(dedupe([a, b], DedupeConfig(threshold=0.3, bands=64)).cards) == 2


def test_a_threshold_the_lsh_cannot_reach_is_refused() -> None:
    with pytest.raises(ValueError, match="bands"):
        DedupeConfig(threshold=0.3, num_perm=128, bands=16)  # rows=8: 50% point is ~0.71


# ---- missing tests from the review ---------------------------------------------------------


async def test_a_cursor_for_another_plan_or_total_is_ignored(tmp_path: Path) -> None:
    raw = write_jsonl(tmp_path / "r.jsonl", list(synthetic_items(120, seed=3)))
    state = tmp_path / "st"
    store = StateStore(state)
    store.save(Cursor("f" * 64, 100, 120))  # a digest that is not this plan's
    kb = FakeKb()
    report = await run_ingestion(
        JsonlSource(raw),
        kb,
        state=store,
        dedupe_config=DedupeConfig(),
        today=TODAY,
    )
    assert report.resumed_from == 0 and report.added == 120


async def test_a_422_after_a_partial_success_keeps_the_stored_cards(tmp_path: Path) -> None:
    items = list(synthetic_items(120, seed=3))
    raw = write_jsonl(tmp_path / "r.jsonl", items)
    from ingestion.urls import normalise_url

    kb = FakeKb()
    kb.reject_urls = {normalise_url(items[100]["url"])}  # in the third batch
    report = await run_ingestion(
        JsonlSource(raw),
        kb,
        state=StateStore(tmp_path / "st"),
        dedupe_config=DedupeConfig(),
        today=TODAY,
    )
    assert report.status == "ok" and report.dropped == 1 and report.added == 119


def test_np_seed_may_reach_only_kb_on_8000() -> None:
    docs = list(yaml.safe_load_all((ROOT / "k8s/base/netpol/services.yaml").read_text()))
    (policy,) = [d for d in docs if d and d["metadata"]["name"] == "np-seed"]
    assert policy["spec"]["ingress"] == []
    (rule,) = policy["spec"]["egress"]
    assert [t["podSelector"]["matchLabels"]["app"] for t in rule["to"]] == ["kb"]
    assert [p["port"] for p in rule["ports"]] == [8000]


def test_the_docker_build_context_keeps_snapshots_and_drops_temp_files() -> None:
    patterns = [
        ln.strip()
        for ln in (ROOT / ".dockerignore").read_text().splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    assert patterns.index("data") < patterns.index("!data/snapshots")
    assert patterns.index("!data/snapshots") < patterns.index("data/snapshots/.tmp-*")
    assert "data/snapshots/*.partial" in patterns
    git = (ROOT / ".gitignore").read_text().splitlines()
    assert "data/snapshots/.tmp-*" in git and "data/snapshots/*.partial" in git


def test_the_seed_job_fails_fast_on_config_and_snapshot_errors() -> None:
    job = yaml.safe_load((ROOT / "k8s/jobs/seed.yaml").read_text().split("\n", 1)[1])
    assert job["spec"]["backoffLimit"] == 2
    pod = job["spec"]["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    (rule,) = job["spec"]["podFailurePolicy"]["rules"]
    assert rule["action"] == "FailJob"
    assert set(rule["onExitCodes"]["values"]) == {2, 3, 4}
    assert pod["containers"][0]["imagePullPolicy"] == "Always"


def test_seed_sh_defaults_to_the_commit_sha_and_pins_the_pull_policy() -> None:
    text = (ROOT / "scripts/seed.sh").read_text()
    assert "git rev-parse --short=7 HEAD" in text and "IfNotPresent" in text


# ---- M6 write endpoints need X-Ingest-Token ----------------------------------------------------


async def test_the_ingest_token_header_is_sent_on_ingest_only() -> None:
    import httpx

    from ingestion.client import HttpKbClient

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = {"results": []} if request.method == "POST" else {"items": [], "total": 0}
        return httpx.Response(200, json=body)

    kb = HttpKbClient(
        "http://kb:8000",
        internal_token="a",
        ingest_token="b",
        transport=httpx.MockTransport(handler),
    )
    await kb.ingest([])
    await kb.list_cards(limit=1, offset=0)
    assert seen[0].headers["x-ingest-token"] == "b" and seen[0].headers["x-internal-token"] == "a"
    assert "x-ingest-token" not in seen[1].headers


def test_run_and_restore_exit_two_without_an_ingest_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("KB_URL", "http://kb:8000")
    monkeypatch.delenv("INGEST_TOKEN", raising=False)
    raw = write_jsonl(tmp_path / "r.jsonl", list(synthetic_items(2)))
    assert main(["run", "--input", str(raw)], kb_factory=lambda s: FakeKb()) == 2
    assert "INGEST_TOKEN" in capsys.readouterr().err
    assert main(["restore", str(tmp_path)], kb_factory=lambda s: FakeKb()) == 2


def test_a_wrong_token_is_a_clear_exit_two_and_never_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("KB_URL", "http://kb:8000")
    monkeypatch.setenv("INGEST_TOKEN", "super-secret-value")
    kb = FakeKb()
    kb.unauthorized = True
    raw = write_jsonl(tmp_path / "r.jsonl", list(synthetic_items(2)))
    assert main(["run", "--input", str(raw)], kb_factory=lambda s: kb) == 2
    captured = capsys.readouterr()
    assert (
        "INGEST_TOKEN" in captured.err and "super-secret-value" not in captured.out + captured.err
    )


def test_snapshot_does_not_need_the_ingest_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KB_URL", "http://kb:8000")
    monkeypatch.delenv("INGEST_TOKEN", raising=False)
    assert main(["snapshot", "--out-dir", str(tmp_path)], kb_factory=lambda s: FakeKb()) == 3
