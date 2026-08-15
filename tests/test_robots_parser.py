import asyncio

import pytest

from src.fetch_result import FetchResult
from src.robots_parser import RobotsParser


@pytest.mark.asyncio
async def test_parses_permissions_and_crawl_delay() -> None:
    content = """
User-agent: *
Disallow: /private/
Allow: /private/public/
Crawl-delay: 2
"""

    async def fetcher(url: str) -> FetchResult:
        assert url == "https://example.com/robots.txt"
        return FetchResult.success(url, content)

    parser = RobotsParser(fetcher=fetcher)
    result = await parser.fetch_robots("HTTPS://Example.COM:443/catalog")

    assert result == {
        "origin": "https://example.com",
        "robots_url": "https://example.com/robots.txt",
        "status": 200,
        "available": True,
        "error": None,
        "from_cache": False,
    }
    assert parser.can_fetch("https://example.com/catalog") is True
    assert parser.can_fetch("https://example.com/private/page") is False
    assert parser.can_fetch("https://example.com/private/public/page") is True
    assert parser.get_crawl_delay() == 2.0


@pytest.mark.asyncio
async def test_uses_rules_for_specific_user_agent() -> None:
    content = """
User-agent: FriendlyBot
Disallow: /bot-private/
Crawl-delay: 4

User-agent: *
Disallow: /public-private/
Crawl-delay: 1
"""

    async def fetcher(url: str) -> FetchResult:
        return FetchResult.success(url, content)

    parser = RobotsParser(fetcher=fetcher)
    await parser.fetch_robots("https://example.com")

    assert (
        parser.can_fetch(
            "https://example.com/bot-private/page",
            user_agent="FriendlyBot",
        )
        is False
    )
    assert parser.can_fetch("https://example.com/bot-private/page") is True
    assert parser.get_crawl_delay("FriendlyBot") == 4.0
    assert parser.get_crawl_delay("*") == 1.0


@pytest.mark.asyncio
async def test_reuses_cache_for_pages_on_same_origin() -> None:
    calls: list[str] = []

    async def fetcher(url: str) -> FetchResult:
        calls.append(url)
        return FetchResult.success(url, "User-agent: *\nDisallow:")

    parser = RobotsParser(fetcher=fetcher)
    first = await parser.fetch_robots("https://example.com/first")
    second = await parser.fetch_robots("https://example.com/second")

    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert calls == ["https://example.com/robots.txt"]
    assert parser.cached_origins == frozenset({"https://example.com"})
    assert parser.get_stats()["cache_hits"] == 1


@pytest.mark.asyncio
async def test_concurrent_calls_fetch_same_origin_once() -> None:
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()
    calls = 0

    async def fetcher(url: str) -> FetchResult:
        nonlocal calls
        calls += 1
        fetch_started.set()
        await release_fetch.wait()
        return FetchResult.success(url, "User-agent: *\nDisallow:")

    parser = RobotsParser(fetcher=fetcher)
    first = asyncio.create_task(parser.fetch_robots("https://example.com/one"))
    await fetch_started.wait()
    second = asyncio.create_task(parser.fetch_robots("https://example.com/two"))
    await asyncio.sleep(0)

    release_fetch.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert calls == 1
    assert {first_result["from_cache"], second_result["from_cache"]} == {
        False,
        True,
    }


@pytest.mark.asyncio
async def test_different_origins_have_independent_cache_entries() -> None:
    calls: list[str] = []

    async def fetcher(url: str) -> FetchResult:
        calls.append(url)
        return FetchResult.success(url, "User-agent: *\nDisallow:")

    parser = RobotsParser(fetcher=fetcher)
    await parser.fetch_robots("https://one.example/page")
    await parser.fetch_robots("https://two.example/page")

    assert calls == [
        "https://one.example/robots.txt",
        "https://two.example/robots.txt",
    ]
    assert parser.get_stats()["cached_origins"] == 2


@pytest.mark.asyncio
async def test_missing_robots_file_allows_crawling_and_is_cached() -> None:
    calls = 0

    async def fetcher(url: str) -> FetchResult:
        nonlocal calls
        calls += 1
        return FetchResult.http_error(url, 404, content="Not Found")

    parser = RobotsParser(fetcher=fetcher)
    result = await parser.fetch_robots("https://example.com")
    await parser.fetch_robots("https://example.com/another")

    assert result["available"] is False
    assert result["error"] == "HTTP 404"
    assert parser.can_fetch("https://example.com/anything") is True
    assert calls == 1


@pytest.mark.asyncio
async def test_explicit_access_denial_blocks_entire_origin() -> None:
    async def fetcher(url: str) -> FetchResult:
        return FetchResult.http_error(url, 403, content="Forbidden")

    parser = RobotsParser(fetcher=fetcher)
    await parser.fetch_robots("https://example.com")

    assert parser.can_fetch("https://example.com/page") is False
    assert parser.get_stats()["blocked_checks"] == 1


@pytest.mark.asyncio
async def test_network_failure_uses_fail_open_policy_and_is_cached() -> None:
    calls = 0

    async def fetcher(url: str) -> FetchResult:
        nonlocal calls
        calls += 1
        return FetchResult.network_error(url, "ClientConnectionError: connection lost")

    parser = RobotsParser(fetcher=fetcher)
    result = await parser.fetch_robots("https://example.com")
    await parser.fetch_robots("https://example.com/other")

    assert result["status"] is None
    assert result["available"] is False
    assert "ClientConnectionError" in str(result["error"])
    assert parser.can_fetch("https://example.com/page") is True
    assert calls == 1


@pytest.mark.asyncio
async def test_delay_requires_origin_when_multiple_are_cached() -> None:
    async def fetcher(url: str) -> FetchResult:
        delay = 1 if "one.example" in url else 3
        return FetchResult.success(url, f"User-agent: *\nCrawl-delay: {delay}")

    parser = RobotsParser(fetcher=fetcher)
    await parser.fetch_robots("https://one.example")
    await parser.fetch_robots("https://two.example")

    with pytest.raises(ValueError, match="base_url"):
        parser.get_crawl_delay()

    assert parser.get_crawl_delay(base_url="https://two.example/page") == 3.0


def test_queries_require_rules_to_be_loaded_first() -> None:
    parser = RobotsParser(fetcher=None)

    with pytest.raises(RuntimeError, match="not been fetched"):
        parser.can_fetch("https://example.com/page")
    with pytest.raises(RuntimeError, match="not been fetched"):
        parser.get_crawl_delay()


@pytest.mark.parametrize(
    "url",
    ["", "example.com", "ftp://example.com", "https:///missing", "https://x:bad"],
)
def test_invalid_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="url"):
        RobotsParser.get_origin(url)


def test_origin_preserves_non_default_port_and_ipv6() -> None:
    assert RobotsParser.get_origin("https://example.com:8443/path") == (
        "https://example.com:8443"
    )
    assert RobotsParser.get_origin("http://[::1]:8080/path") == "http://[::1]:8080"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"user_agent": ""}, "user_agent"),
        ({"timeout": 0}, "timeout"),
        ({"timeout": True}, "timeout"),
        ({"timeout": float("inf")}, "timeout"),
        ({"timeout": float("nan")}, "timeout"),
        ({"fetcher": "not callable"}, "fetcher"),
    ],
)
def test_invalid_configuration_is_rejected(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        RobotsParser(**kwargs)
