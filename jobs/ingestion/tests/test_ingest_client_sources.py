import gzip
import json
from pathlib import Path

import httpx
import pytest

from common.errors import Unauthorized, UpstreamError, UpstreamTimeout, ValidationFailed
from ingestion.client import HttpKbClient
from ingestion.config import Settings
from ingestion.models import RawItem, Rejection
from ingestion.sources import JsonlSource, row_to_item
from ingestion.state import Cursor, StateStore


def client(handler: httpx.MockTransport, **kw: object) -> HttpKbClient:
    return HttpKbClient(
        "http://kb:8000",
        internal_token="tok",
        backoff_s=0.001,
        transport=handler,
        **kw,  # type: ignore[arg-type]
    )


async def test_ingest_posts_cards_with_the_internal_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [{"card_id": "card_aaaaaaaa", "created": True, "changed": True}]
                * len(body["cards"])
            },
        )

    out = await client(httpx.MockTransport(handler)).ingest([{"url": "https://a.com/x"}])
    assert out[0].created and out[0].card_id == "card_aaaaaaaa"
    assert seen[0].headers["x-internal-token"] == "tok"
    assert seen[0].url.path == "/ingest"


async def test_transient_failures_are_retried_at_most_twice() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    with pytest.raises(UpstreamError):
        await client(httpx.MockTransport(handler), max_retries=5).ingest([{}])
    assert calls == 3  # 1 try + 2 retries, even if asked for more


async def test_a_retry_that_succeeds_returns_the_result() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"results": []})

    assert await client(httpx.MockTransport(handler)).ingest([]) == []
    assert calls == 3


async def test_a_timeout_is_an_error_not_a_hang() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(UpstreamTimeout):
        await client(httpx.MockTransport(handler), max_retries=0).ingest([{}])


@pytest.mark.parametrize(("status", "error"), [(422, ValidationFailed), (401, Unauthorized)])
async def test_client_errors_are_not_retried(status: int, error: type[Exception]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    with pytest.raises(error):
        await client(httpx.MockTransport(handler)).ingest([{}])
    assert calls == 1


async def test_list_cards_returns_the_page_and_total() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["limit"] == "10" and request.url.params["offset"] == "20"
        return httpx.Response(200, json={"items": [{"id": "x"}], "total": 21})

    items, total = await client(httpx.MockTransport(handler)).list_cards(limit=10, offset=20)
    assert items == [{"id": "x"}] and total == 21


# ---- settings ----------------------------------------------------------------------------------


def test_settings_cap_batch_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGESTION_BATCH_SIZE", "51")
    with pytest.raises(ValueError):
        Settings()
    monkeypatch.setenv("INGESTION_BATCH_SIZE", "50")
    monkeypatch.setenv("INGESTION_MAX_RETRIES", "3")
    with pytest.raises(ValueError):
        Settings()


def test_settings_defaults_have_no_hosts_or_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("KB_URL", "DATABASE_URL", "INTERNAL_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    s = Settings()
    assert s.kb_url is None and s.internal_token is None
    assert s.dedupe_config().threshold == 0.9


# ---- sources -----------------------------------------------------------------------------------


def test_jsonl_source_reads_plain_and_gzip(tmp_path: Path) -> None:
    line = json.dumps({"source": "github", "url": "https://a.com/x", "title": "t"}) + "\n"
    plain = tmp_path / "a.jsonl"
    plain.write_text(line + "\n" + line)
    packed = tmp_path / "a.jsonl.gz"
    packed.write_bytes(gzip.compress((line * 3).encode()))
    assert [type(r) for r in JsonlSource(plain).read()] == [RawItem, RawItem]
    assert len(list(JsonlSource(packed).read())) == 3


def test_jsonl_source_survives_garbage_bytes_and_a_missing_final_newline(tmp_path: Path) -> None:
    path = tmp_path / "g.jsonl"
    path.write_bytes(b'\xff\xfe{"source":"github"}\n{"source":"github","url":"https://a.com/x"}')
    rows = list(JsonlSource(path).read())
    assert isinstance(rows[0], Rejection) and isinstance(rows[1], RawItem)


def test_missing_input_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list(JsonlSource(tmp_path / "nope.jsonl").read())


def test_raw_pages_row_with_a_json_body() -> None:
    row = {
        "id": 1,
        "source": "reddit",
        "url": "https://reddit.com/r/x/comments/1",
        "fetched_at": "2026-02-01T00:00:00+00:00",
        "body": json.dumps({"title": "T", "text": "body", "score": 5}),
    }
    item = row_to_item(0, row)
    assert isinstance(item, RawItem)
    assert (item.title, item.score, item.source) == ("T", 5, "reddit")


def test_raw_pages_row_with_a_plain_text_or_missing_body() -> None:
    plain = row_to_item(0, {"source": "github", "url": "https://a.com/x", "body": "just text"})
    assert isinstance(plain, RawItem) and plain.text == "just text"
    empty = row_to_item(1, {"source": "github", "url": "https://a.com/x", "body": None})
    assert isinstance(empty, RawItem)
    bad = row_to_item(2, {"source": None, "url": None, "body": {}})
    assert isinstance(bad, Rejection)


# ---- state -------------------------------------------------------------------------------------


def test_cursor_roundtrip_and_rejects_nonsense(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    assert store.load() is None
    store.save(Cursor("d", 5, 10))
    assert store.load() == Cursor("d", 5, 10)
    (tmp_path / "cursor.json").write_text(json.dumps({"digest": "d", "done": 11, "total": 10}))
    assert store.load() is None
    (tmp_path / "cursor.json").write_text("[]")
    assert store.load() is None
    store.clear()
    store.clear()  # clearing twice is fine


def test_scouts_raw_pages_row_with_a_jsonb_dict_body() -> None:
    from datetime import UTC, datetime

    row = {
        "id": 7,
        "source": "hackernews",
        "url": "https://news.ycombinator.com/item?id=1",
        "fetched_at": datetime(2026, 2, 1, tzinfo=UTC),
        "status_code": 200,
        "content_hash": "abc",
        "processed": False,
        "body": {"title": "Show HN: X", "text": "hello", "score": 12, "metadata": {"a": 1}},
    }
    item = row_to_item(0, row)
    assert isinstance(item, RawItem)
    assert (item.title, item.score, item.source) == ("Show HN: X", 12, "hackernews")
    assert item.fetched_at is not None


def test_raw_pages_queries_are_schema_qualified_and_the_schema_is_validated() -> None:
    from ingestion.sources import queries

    read, mark = queries("scouts")
    assert "FROM scouts.raw_pages" in read and "NOT processed" in read
    assert "UPDATE scouts.raw_pages" in mark
    for bad in ("scouts; drop table x", "Scouts", "a.b", ""):
        with pytest.raises(ValueError):
            queries(bad)
