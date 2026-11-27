import pytest

from svc_scouts.models.runs import Page
from svc_scouts.services.errors import (
    BudgetExhausted,
    FetchFailed,
    MalformedPayload,
    SourceRateLimited,
)
from svc_scouts.testing import FakeFetcher, make_ctx

URL = "https://hn.algolia.com/x"


async def test_json_body_is_returned_and_counted() -> None:
    fetcher = FakeFetcher()
    fetcher.add_json(URL, {"a": 1})
    ctx = make_ctx("hackernews", fetcher)
    assert await ctx.get(URL) == {"a": 1}
    assert ctx.requests == 1


async def test_json_in_the_text_field_is_parsed() -> None:
    fetcher = FakeFetcher({URL: Page(status=200, text="[1, 2]")})
    assert await make_ctx("hackernews", fetcher).get(URL) == [1, 2]


@pytest.mark.parametrize("page", [Page(status=200), Page(status=200, text="<html>nope</html>")])
async def test_empty_or_non_json_bodies_are_malformed(page: Page) -> None:
    ctx = make_ctx("hackernews", FakeFetcher({URL: page}))
    with pytest.raises(MalformedPayload):
        await ctx.get(URL)


@pytest.mark.parametrize("status", [429, 403])
async def test_rate_limit_statuses_stop_the_run(status: int) -> None:
    ctx = make_ctx("github", FakeFetcher({URL: Page(status=status)}))
    with pytest.raises(SourceRateLimited):
        await ctx.get(URL)


@pytest.mark.parametrize("status", [400, 404, 500, 503])
async def test_other_error_statuses_are_fetch_failures(status: int) -> None:
    ctx = make_ctx("github", FakeFetcher({URL: Page(status=status)}))
    with pytest.raises(FetchFailed) as caught:
        await ctx.get(URL)
    assert caught.value.status == status


async def test_the_request_budget_is_exact() -> None:
    fetcher = FakeFetcher()
    fetcher.add_json(URL, {})
    ctx = make_ctx("hackernews", fetcher, max_requests=2)
    await ctx.get(URL)
    await ctx.get(URL)
    with pytest.raises(BudgetExhausted):
        await ctx.get(URL)
    assert len(fetcher.calls) == 2  # the refused call never reached M5


async def test_flagged_pages_are_counted() -> None:
    fetcher = FakeFetcher()
    fetcher.add_json(URL, {}, flagged=True)
    ctx = make_ctx("hackernews", fetcher)
    await ctx.get(URL)
    assert ctx.flagged == 1


def test_malformed_items_are_dropped_and_counted_not_raised() -> None:
    ctx = make_ctx("hackernews", FakeFetcher())
    assert not ctx.emit(url="javascript:x", title="t", platform="p")
    assert not ctx.emit(url="https://e.com/a", title="", platform="p")
    assert not ctx.emit(url="https://e.com/a", title="t")  # platform missing
    assert not ctx.emit(url="https://e.com/a", title="t", platform="p", bogus=1)
    assert ctx.dropped == 4 and ctx.items == []


def test_duplicate_urls_in_one_run_are_kept_once_without_counting_as_dropped() -> None:
    ctx = make_ctx("hackernews", FakeFetcher())
    assert ctx.emit(url="https://e.com/a", title="t", platform="p")
    assert not ctx.emit(url="https://e.com/a", title="t2", platform="p")
    assert len(ctx.items) == 1 and ctx.dropped == 0


def test_the_item_limit_stops_right_after_the_limit_item() -> None:
    ctx = make_ctx("hackernews", FakeFetcher(), limit=2)
    ctx.emit(url="https://e.com/1", title="t", platform="p")
    with pytest.raises(BudgetExhausted):
        ctx.emit(url="https://e.com/2", title="t", platform="p")
    assert len(ctx.items) == 2


def test_the_configured_per_run_cap_wins_over_a_larger_limit() -> None:
    from svc_scouts.models.sources import SourceCfg
    from svc_scouts.services.context import Credentials, ScoutContext
    from svc_scouts.testing import NOW

    ctx = ScoutContext(
        source="s",
        cfg=SourceCfg(max_items_per_run=3),
        fetcher=FakeFetcher(),
        credentials=Credentials(),
        cursor={},
        run_id="r",
        limit=10_000,
        now=NOW,
    )
    assert ctx.limit == 3


def test_credentials_never_show_in_repr() -> None:
    from pydantic import SecretStr

    from svc_scouts.services.context import Credentials

    shown = repr(Credentials(github_token=SecretStr("ghp_supersecret")))
    assert "ghp_supersecret" not in shown
