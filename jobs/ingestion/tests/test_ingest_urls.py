import pytest

from ingestion.urls import normalise_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://GitHub.com/Foo/Bar/", "https://github.com/Foo/Bar"),
        ("https://github.com/foo/bar?utm_source=x&utm_medium=y", "https://github.com/foo/bar"),
        ("https://github.com/foo/bar#readme", "https://github.com/foo/bar"),
        ("https://www.github.com/foo/bar", "https://github.com/foo/bar"),
        (
            "https://old.reddit.com/r/SaaS/comments/abc/t/",
            "https://reddit.com/r/SaaS/comments/abc/t",
        ),
        ("https://np.reddit.com/r/SaaS/comments/abc/t", "https://reddit.com/r/SaaS/comments/abc/t"),
        (
            "https://www.reddit.com/r/SaaS/comments/abc/t",
            "https://reddit.com/r/SaaS/comments/abc/t",
        ),
        ("https://m.reddit.com/r/SaaS/comments/abc/t", "https://reddit.com/r/SaaS/comments/abc/t"),
        ("https://www.producthunt.com/posts/x?ref=ph", "https://producthunt.com/posts/x"),
        (
            "https://news.ycombinator.com/item?id=5&utm_campaign=z",
            "https://news.ycombinator.com/item?id=5",
        ),
        ("https://example.com:443/a?b=2&a=1", "https://example.com/a?a=1&b=2"),
    ],
)
def test_known_spellings_collapse_to_one_url(raw: str, expected: str) -> None:
    assert normalise_url(raw) == expected


def test_normalising_twice_changes_nothing() -> None:
    once = normalise_url("https://OLD.reddit.com/r/x/comments/1/t/?utm_source=a#c")
    assert normalise_url(once) == once


def test_a_mirror_host_is_not_confused_with_a_lookalike() -> None:
    assert normalise_url("https://www.reddit.com.evil.example/x") == (
        "https://www.reddit.com.evil.example/x"
    )


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "not a url", "ftp://example.com/x", "javascript:alert(1)", "https://", "//x.com"],
)
def test_anything_that_is_not_an_http_page_is_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        normalise_url(bad)


def test_unicode_paths_are_kept() -> None:
    assert normalise_url("https://Example.com/café/") == "https://example.com/café"
