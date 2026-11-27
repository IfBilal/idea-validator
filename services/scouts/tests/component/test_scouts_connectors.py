"""Connectors against recorded payloads (tests/fixtures). No network: a scripted fetcher."""

import json
from typing import Any

import pytest
from pydantic import SecretStr

from svc_scouts.models.runs import ApiRequest, Page
from svc_scouts.services.connectors.github import GitHub
from svc_scouts.services.connectors.hackernews import HackerNews
from svc_scouts.services.connectors.lobsters import Lobsters
from svc_scouts.services.connectors.producthunt import ProductHunt
from svc_scouts.services.connectors.reddit import Reddit
from svc_scouts.services.connectors.stackexchange import StackExchange
from svc_scouts.services.context import Credentials
from svc_scouts.services.errors import (
    BudgetExhausted,
    FetchFailed,
    MalformedPayload,
    SourceRateLimited,
)
from svc_scouts.testing import FakeFetcher, fixture, make_ctx

ALGOLIA = "https://hn.algolia.com/api/v1/search_by_date?tags="
FIREBASE = "https://hacker-news.firebaseio.com/v0"


def hn_fetcher() -> FakeFetcher:
    f = FakeFetcher()
    f.add_json(ALGOLIA + "show_hn", fixture("hn_algolia_show"))
    f.add_json(ALGOLIA + "ask_hn", fixture("hn_algolia_ask"))
    f.add_json(f"{FIREBASE}/showstories.json", fixture("hn_firebase_showstories"))
    f.add_json(f"{FIREBASE}/item/41000050.json", fixture("hn_firebase_item_50"))
    f.add_json(f"{FIREBASE}/item/41000051.json", None, status=200)
    f.routes[f"{FIREBASE}/item/41000051.json"] = Page(status=200, text="null")
    f.add_json(f"{FIREBASE}/item/41000052.json", fixture("hn_firebase_item_52"))
    return f


HN_OPTIONS = {"algolia_tags": ["show_hn", "ask_hn"], "feeds": ["showstories"]}


# ---------------------------------------------------------------- Hacker News
async def test_hn_algolia_keeps_valid_hits_and_drops_malformed_ones() -> None:
    ctx = make_ctx("hackernews", hn_fetcher(), options={"algolia_tags": ["show_hn"]})
    await HackerNews().collect(ctx)
    assert [i.title for i in ctx.items] == [
        "Show HN: Invoice tool for freelancers",
        "Show HN: Text only post",
    ]
    assert ctx.dropped == 3  # no title, no objectID/time, not an object


async def test_hn_item_shape_and_text_only_posts_get_the_discussion_url() -> None:
    ctx = make_ctx("hackernews", hn_fetcher(), options={"algolia_tags": ["show_hn"]})
    await HackerNews().collect(ctx)
    first, second = ctx.items
    assert first.url == "https://example.com/invoice" and first.score == 120
    assert first.platform == "hackernews" and first.metadata["kind"] == "show"
    assert first.created_at is not None and first.created_at.year == 2025
    assert second.url == "https://news.ycombinator.com/item?id=41000002"
    assert second.text == "I built a thing &amp; it helps"
    assert "author" not in json.dumps(first.body())  # no usernames kept


async def test_hn_cursor_advances_only_when_a_feed_was_read_to_its_end() -> None:
    ctx = make_ctx("hackernews", hn_fetcher(), options={"algolia_tags": ["show_hn", "ask_hn"]})
    await HackerNews().collect(ctx)
    assert ctx.cursor["algolia_since"] == {"show_hn": 1760000300, "ask_hn": 1760000400}
    f = hn_fetcher()
    ctx2 = make_ctx("hackernews", f, options={"algolia_tags": ["show_hn"]}, cursor=ctx.cursor)
    await HackerNews().collect(ctx2)
    assert "created_at_i%3E1760000300" in f.calls[0].url


async def test_hn_first_run_asks_for_everything_since_zero() -> None:
    f = hn_fetcher()
    await HackerNews().collect(make_ctx("hackernews", f, options={"algolia_tags": ["show_hn"]}))
    assert "created_at_i%3E0" in f.calls[0].url


async def test_hn_page_cap_keeps_the_cursor_so_the_rest_is_read_next_time() -> None:
    f = FakeFetcher()
    f.add_json(
        ALGOLIA, {"hits": [{"objectID": "1", "title": "t", "created_at_i": 5}], "nbPages": 50}
    )
    ctx = make_ctx("hackernews", f, options={"algolia_tags": ["show_hn"], "algolia_max_pages": 2})
    await HackerNews().collect(ctx)
    assert len(f.calls) == 2 and "algolia_since" not in ctx.cursor


async def test_hn_empty_source_yields_nothing_without_error() -> None:
    f = FakeFetcher()
    f.add_json(ALGOLIA, {"hits": [], "nbPages": 0})
    ctx = make_ctx("hackernews", f, options={"algolia_tags": ["show_hn"]})
    await HackerNews().collect(ctx)
    assert ctx.items == [] and ctx.dropped == 0


@pytest.mark.parametrize("payload", [[], "oops", 7])
async def test_hn_wrong_top_level_shape_is_a_malformed_payload(payload: Any) -> None:
    f = FakeFetcher()
    f.add_json(ALGOLIA, payload)
    ctx = make_ctx("hackernews", f, options={"algolia_tags": ["show_hn"]})
    with pytest.raises(MalformedPayload):
        await HackerNews().collect(ctx)


async def test_hn_hits_that_are_not_a_list_are_malformed() -> None:
    f = FakeFetcher()
    f.add_json(ALGOLIA, {"hits": {"a": 1}, "nbPages": 1})
    with pytest.raises(MalformedPayload):
        await HackerNews().collect(make_ctx("hackernews", f, options={"algolia_tags": ["show_hn"]}))


async def test_hn_server_error_propagates_for_the_breaker() -> None:
    f = FakeFetcher({ALGOLIA: Page(status=503)})
    with pytest.raises(FetchFailed):
        await HackerNews().collect(make_ctx("hackernews", f, options={"algolia_tags": ["show_hn"]}))


async def test_hn_instruction_text_is_stored_as_data_and_triggers_no_extra_fetch() -> None:
    f = hn_fetcher()
    ctx = make_ctx("hackernews", f, options={"algolia_tags": ["ask_hn"]})
    await HackerNews().collect(ctx)
    (item,) = ctx.items
    assert "Ignore all previous instructions" in item.text
    assert [c.url.split("?")[0] for c in f.calls] == [ALGOLIA.split("?")[0]]  # nothing followed


async def test_hn_firebase_feeds_skip_seen_null_dead_and_remember_what_was_read() -> None:
    f = hn_fetcher()
    ctx = make_ctx(
        "hackernews",
        f,
        options={"algolia_tags": [], "feeds": ["showstories"]},
        cursor={"firebase_seen": {"showstories": [41000001]}},
    )
    await HackerNews().collect(ctx)
    assert [i.title for i in ctx.items] == ["Show HN: A fast linter"]
    assert ctx.dropped == 2  # the null item and the dead one
    fetched = [c.url for c in f.calls]
    assert f"{FIREBASE}/item/41000001.json" not in fetched
    assert ctx.cursor["firebase_seen"]["showstories"] == [41000001, 41000050, 41000051, 41000052]


async def test_hn_firebase_per_feed_cap() -> None:
    f = hn_fetcher()
    ctx = make_ctx(
        "hackernews",
        f,
        options={"algolia_tags": [], "feeds": ["showstories"], "firebase_max_items": 1},
        cursor={"firebase_seen": {"showstories": [41000001]}},
    )
    await HackerNews().collect(ctx)
    assert len(f.calls) == 2  # the list plus one item


async def test_hn_request_budget_stops_gracefully_mid_feed() -> None:
    ctx = make_ctx(
        "hackernews", hn_fetcher(), options={"algolia_tags": ["show_hn", "ask_hn"]}, max_requests=1
    )
    with pytest.raises(BudgetExhausted):
        await HackerNews().collect(ctx)
    assert len(ctx.items) == 2  # what was read before the budget ended is kept


# --------------------------------------------------------------------- GitHub
SEARCH = "https://api.github.com/search/repositories"


async def test_github_keeps_real_repos_and_skips_forks_and_malformed_rows() -> None:
    f = FakeFetcher()
    f.add_json(SEARCH, fixture("github_search"))
    ctx = make_ctx("github", f, options={"topics": ["saas"], "min_stars": 50})
    await GitHub().collect(ctx)
    (repo,) = ctx.items
    assert repo.title == "acme/notes" and repo.score == 420 and repo.platform == "github"
    assert repo.text == "Self-hosted notes saas self-hosted"
    assert repo.metadata["stars"] == 420 and repo.metadata["language"] == "Go"
    assert ctx.dropped == 2
    assert "topic%3Asaas" in f.calls[0].url and "stars%3A%3E%3D50" in f.calls[0].url
    assert "owner" not in json.dumps(repo.body())


async def test_github_second_run_only_asks_for_recently_pushed_repos() -> None:
    f = FakeFetcher()
    f.add_json(SEARCH, fixture("github_search"))
    ctx = make_ctx("github", f, options={"topics": ["saas"]})
    await GitHub().collect(ctx)
    assert ctx.cursor["pushed_since"] == "2026-11-27"
    f2 = FakeFetcher()
    f2.add_json(SEARCH, fixture("github_search"))
    await GitHub().collect(make_ctx("github", f2, options={"topics": ["saas"]}, cursor=ctx.cursor))
    assert "pushed%3A%3E%3D2026-11-27" in f2.calls[0].url


async def test_github_pages_only_while_a_page_is_full() -> None:
    full = {
        "items": [
            {
                "full_name": f"o/r{i}",
                "html_url": f"https://github.com/o/r{i}",
                "stargazers_count": 60,
            }
            for i in range(100)
        ]
    }
    f = FakeFetcher()
    f.add_json(SEARCH + "?q=topic%3Asaas", full)
    ctx = make_ctx("github", f, options={"topics": ["saas"], "pages_per_topic": 3})
    await GitHub().collect(ctx)
    assert len(f.calls) == 3 and len(ctx.items) == 100  # same rows each page: deduplicated


async def test_github_token_goes_in_a_header_never_in_the_url_or_the_items() -> None:
    f = FakeFetcher()
    f.add_json(SEARCH, fixture("github_search"))
    creds = Credentials(github_token=SecretStr("ghp_secret"))
    ctx = make_ctx("github", f, options={"topics": ["saas"]}, credentials=creds)
    await GitHub().collect(ctx)
    call = f.calls[0]
    assert call.headers == {"Authorization": "Bearer ghp_secret"}
    assert "ghp_secret" not in call.url and "ghp_secret" not in repr(call)
    assert "ghp_secret" not in json.dumps([i.body() for i in ctx.items])


async def test_github_without_a_token_sends_no_headers() -> None:
    f = FakeFetcher()
    f.add_json(SEARCH, {"items": []})
    await GitHub().collect(make_ctx("github", f, options={"topics": ["saas"]}))
    assert f.calls[0].headers == {}


async def test_github_secondary_rate_limit_stops_the_run() -> None:
    f = FakeFetcher({SEARCH: Page(status=403, text="rate limit")})
    with pytest.raises(SourceRateLimited):
        await GitHub().collect(make_ctx("github", f, options={"topics": ["saas", "ai"]}))
    assert len(f.calls) == 1  # no hammering


async def test_github_empty_topic_list_and_empty_results() -> None:
    f = FakeFetcher()
    ctx = make_ctx("github", f, options={"topics": []})
    await GitHub().collect(ctx)
    assert f.calls == [] and ctx.items == []
    f.add_json(SEARCH, {"total_count": 0, "items": []})
    await GitHub().collect(make_ctx("github", f, options={"topics": ["x"]}))


@pytest.mark.parametrize("payload", [[], {"items": "nope"}, {"items": {"a": 1}}])
async def test_github_malformed_payloads(payload: Any) -> None:
    f = FakeFetcher()
    f.add_json(SEARCH, payload)
    with pytest.raises(MalformedPayload):
        await GitHub().collect(make_ctx("github", f, options={"topics": ["saas"]}))


# ------------------------------------------------------------------- Lobsters
async def test_lobsters_items_drop_usernames_and_fall_back_to_the_discussion_url() -> None:
    f = FakeFetcher()
    f.add_json("https://lobste.rs/hottest.json", fixture("lobsters_hottest"))
    ctx = make_ctx("lobsters", f, options={"feeds": ["hottest"], "pages": 1})
    await Lobsters().collect(ctx)
    crm, ask = ctx.items
    assert crm.url == "https://example.org/crm" and crm.metadata["discussion_url"].endswith(
        "abc123"
    )
    assert ask.url == "https://lobste.rs/s/def456" and ask.text == "text"
    assert ctx.dropped == 1
    assert "alice" not in json.dumps([i.body() for i in ctx.items])
    assert ctx.cursor["newest_created_at"] == "2026-11-26T16:00:00+00:00"


async def test_lobsters_visits_feeds_and_tags_and_pages() -> None:
    f = FakeFetcher()
    f.add_json(
        "https://lobste.rs/",
        [
            {
                "short_id": "a",
                "short_id_url": "https://lobste.rs/s/a",
                "title": "t",
                "created_at": "2026-11-26T10:00:00Z",
            }
        ],
    )
    ctx = make_ctx("lobsters", f, options={"feeds": ["hottest"], "tags": ["show"], "pages": 2})
    await Lobsters().collect(ctx)
    assert [c.url for c in f.calls] == [
        "https://lobste.rs/hottest.json",
        "https://lobste.rs/hottest.json?page=2",
        "https://lobste.rs/t/show.json",
        "https://lobste.rs/t/show.json?page=2",
    ]


async def test_lobsters_stops_paging_at_a_page_older_than_the_last_run() -> None:
    f = FakeFetcher()
    f.add_json("https://lobste.rs/hottest.json", fixture("lobsters_hottest"))
    ctx = make_ctx(
        "lobsters",
        f,
        options={"feeds": ["hottest"], "pages": 5},
        cursor={"newest_created_at": "2026-12-01T00:00:00+00:00"},
    )
    await Lobsters().collect(ctx)
    assert len(f.calls) == 1  # the page had nothing new -> no page 2


async def test_lobsters_empty_feed_and_malformed_shape() -> None:
    f = FakeFetcher()
    f.add_json("https://lobste.rs/hottest.json", [])
    ctx = make_ctx("lobsters", f, options={"feeds": ["hottest"], "pages": 3})
    await Lobsters().collect(ctx)
    assert ctx.items == [] and len(f.calls) == 1
    f.add_json("https://lobste.rs/hottest.json", {"error": "x"})
    with pytest.raises(MalformedPayload):
        await Lobsters().collect(make_ctx("lobsters", f, options={"feeds": ["hottest"]}))


async def test_lobsters_tag_names_are_url_encoded() -> None:
    f = FakeFetcher()
    f.add_json("https://lobste.rs/", [])
    await Lobsters().collect(
        make_ctx("lobsters", f, options={"feeds": [], "tags": ["a/b?c"], "pages": 1})
    )
    assert f.calls[0].url == "https://lobste.rs/t/a%2Fb%3Fc.json"


# -------------------------------------------------------------- Stack Exchange
SE = "https://api.stackexchange.com/2.3/search/advanced"
SE_OPTIONS = {"sites": ["stackoverflow"], "queries": ["is there a tool"]}


async def test_stackexchange_strips_html_scripts_and_keeps_the_licence() -> None:
    f = FakeFetcher()
    f.add_json(SE, fixture("stackexchange_search"))
    ctx = make_ctx("stackexchange", f, options=SE_OPTIONS)
    await StackExchange().collect(ctx)
    (q,) = ctx.items
    assert q.text == "I need a tool that does X &amp; Y."
    assert q.metadata["licence"] == "CC BY-SA 4.0" and q.metadata["site"] == "stackoverflow"
    assert q.score == 15 and ctx.dropped == 1
    assert "bob" not in json.dumps(q.body())
    assert ctx.cursor["fromdate"] == 1760000000


async def test_stackexchange_resumes_from_the_cursor() -> None:
    f = FakeFetcher()
    f.add_json(SE, {"items": []})
    await StackExchange().collect(
        make_ctx("stackexchange", f, options=SE_OPTIONS, cursor={"fromdate": 1760000000})
    )
    assert "fromdate=1760000000" in f.calls[0].url


async def test_stackexchange_backoff_stops_the_run_but_keeps_what_was_read() -> None:
    f = FakeFetcher()
    body = fixture("stackexchange_search")
    body["backoff"] = 30
    f.add_json(SE, body)
    ctx = make_ctx("stackexchange", f, options={"sites": ["a", "b"], "queries": ["q"]})
    with pytest.raises(SourceRateLimited) as caught:
        await StackExchange().collect(ctx)
    assert caught.value.retry_after == 30 and len(ctx.items) == 1 and len(f.calls) == 1


async def test_stackexchange_error_object_is_a_failure() -> None:
    f = FakeFetcher()
    f.add_json(SE, {"error_id": 502, "error_message": "throttle_violation"})
    with pytest.raises(FetchFailed):
        await StackExchange().collect(make_ctx("stackexchange", f, options=SE_OPTIONS))


async def test_stackexchange_without_queries_makes_no_calls() -> None:
    f = FakeFetcher()
    ctx = make_ctx("stackexchange", f, options={"sites": ["stackoverflow"], "queries": []})
    await StackExchange().collect(ctx)
    assert f.calls == []


# --------------------------------------------------------------------- Reddit
REDDIT_CREDS = Credentials(
    reddit_client_id=SecretStr("cid"), reddit_client_secret=SecretStr("csecret")
)


def reddit_fetcher() -> FakeFetcher:
    f = FakeFetcher()
    f.add_json("https://www.reddit.com/api/v1/access_token", fixture("reddit_token"))
    f.add_json("https://oauth.reddit.com/r/SideProject/top", fixture("reddit_top"))
    return f


def test_reddit_and_producthunt_stay_off_without_credentials() -> None:
    assert not Reddit().credentials_ok(Credentials())
    assert not Reddit().credentials_ok(Credentials(reddit_client_id=SecretStr("only-id")))
    assert Reddit().credentials_ok(REDDIT_CREDS)
    assert not ProductHunt().credentials_ok(Credentials())
    assert ProductHunt().credentials_ok(Credentials(producthunt_token=SecretStr("t")))


async def test_reddit_parses_posts_skips_nsfw_and_stickies_and_marks_training_off() -> None:
    f = reddit_fetcher()
    ctx = make_ctx("reddit", f, options={"subreddits": ["SideProject"]}, credentials=REDDIT_CREDS)
    await Reddit().collect(ctx)
    habit, removed = ctx.items
    assert habit.url == "https://www.reddit.com/r/SideProject/comments/a1/i_built/"
    assert habit.score == 210 and habit.metadata["subreddit"] == "SideProject"
    assert habit.metadata["training_allowed"] is False and habit.metadata["retrieval_only"] is True
    assert removed.text == ""  # "[removed]" is not content
    assert ctx.dropped == 2
    assert "u1" not in json.dumps(habit.body())


async def test_reddit_token_request_is_a_post_with_basic_auth_and_marked_auth_required() -> None:
    f = reddit_fetcher()
    ctx = make_ctx("reddit", f, options={"subreddits": ["SideProject"]}, credentials=REDDIT_CREDS)
    await Reddit().collect(ctx)
    token_call, listing = f.calls
    assert token_call.method == "POST" and token_call.auth_required
    assert token_call.headers["Authorization"].startswith("Basic ")
    assert token_call.body == "grant_type=client_credentials"
    assert listing.headers == {"Authorization": "Bearer tok-abc"} and listing.auth_required
    assert "csecret" not in repr(token_call) and "tok-abc" not in repr(listing)


@pytest.mark.parametrize("token_payload", [{}, {"access_token": ""}, {"access_token": 5}, []])
async def test_reddit_bad_token_answers_are_failures(token_payload: Any) -> None:
    f = FakeFetcher()
    f.add_json("https://www.reddit.com/api/v1/access_token", token_payload)
    ctx = make_ctx("reddit", f, options={"subreddits": ["x"]}, credentials=REDDIT_CREDS)
    with pytest.raises((FetchFailed, MalformedPayload)):
        await Reddit().collect(ctx)


async def test_reddit_malformed_listing() -> None:
    f = reddit_fetcher()
    f.add_json("https://oauth.reddit.com/r/SideProject/top", {"data": {"children": "x"}})
    ctx = make_ctx("reddit", f, options={"subreddits": ["SideProject"]}, credentials=REDDIT_CREDS)
    with pytest.raises(MalformedPayload):
        await Reddit().collect(ctx)


async def test_reddit_without_credentials_fails_before_any_call() -> None:
    f = reddit_fetcher()
    with pytest.raises(FetchFailed):
        await Reddit().collect(make_ctx("reddit", f, options={"subreddits": ["SideProject"]}))
    assert f.calls == []


# ---------------------------------------------------------------- Product Hunt
PH = "https://api.producthunt.com/v2/api/graphql"
PH_CREDS = Credentials(producthunt_token=SecretStr("ph-secret"))


async def test_producthunt_posts_graphql_with_a_bearer_token_and_parses_nodes() -> None:
    f = FakeFetcher()
    f.add_json(PH, fixture("producthunt_posts"))
    ctx = make_ctx("producthunt", f, options={"pages": 3}, credentials=PH_CREDS)
    await ProductHunt().collect(ctx)
    (post,) = ctx.items
    assert post.title == "FocusBuddy" and post.score == 321 and post.platform == "producthunt"
    assert post.text == "Stay focused A pomodoro app"
    assert post.metadata["topics"] == ["Productivity", "Tech"]
    assert ctx.dropped == 2
    (call,) = f.calls
    assert call.method == "POST" and call.auth_required
    assert call.headers["Authorization"] == "Bearer ph-secret"
    assert "query" in json.loads(call.body or "{}")
    assert "ph-secret" not in repr(call)
    assert ctx.cursor["posted_after"].startswith("2026-11-20")


async def test_producthunt_graphql_errors_are_failures() -> None:
    f = FakeFetcher()
    f.add_json(PH, {"errors": [{"message": "rate limited"}]})
    with pytest.raises(FetchFailed):
        await ProductHunt().collect(make_ctx("producthunt", f, credentials=PH_CREDS))


async def test_producthunt_follows_the_cursor_until_the_last_page() -> None:
    pages = [
        {"data": {"posts": {"pageInfo": {"endCursor": "c1", "hasNextPage": True}, "edges": []}}},
        {"data": {"posts": {"pageInfo": {"endCursor": None, "hasNextPage": False}, "edges": []}}},
    ]
    calls: list[ApiRequest] = []

    def route(request: ApiRequest) -> Page:
        calls.append(request)
        return Page(status=200, json_=pages[len(calls) - 1])

    f = FakeFetcher({PH: route})
    await ProductHunt().collect(
        make_ctx("producthunt", f, options={"pages": 5}, credentials=PH_CREDS)
    )
    assert len(calls) == 2
    assert json.loads(calls[1].body or "{}")["variables"]["after"] == "c1"


async def test_producthunt_without_token_fails_before_any_call() -> None:
    f = FakeFetcher()
    with pytest.raises(FetchFailed):
        await ProductHunt().collect(make_ctx("producthunt", f))
    assert f.calls == []
