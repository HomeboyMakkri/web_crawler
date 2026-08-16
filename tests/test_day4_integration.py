from types import TracebackType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from src.crawler import AsyncCrawler


class ResponseContext:
    def __init__(self, body: str, *, status: int = 200) -> None:
        self._response = MagicMock(status=status)
        self._response.raise_for_status = MagicMock()
        self._response.text = AsyncMock(return_value=body)

    async def __aenter__(self) -> Any:
        return self._response

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


@pytest.mark.asyncio
async def test_allowed_request_obeys_robots_delay_before_http_fetch() -> None:
    url = "https://example.com/catalog"
    robots = "User-agent: MyBot\nDisallow: /private\nCrawl-delay: 2"
    crawler = AsyncCrawler(
        respect_robots=True,
        requests_per_second=4.0,
        min_delay=0.5,
        user_agent="MyBot",
    )
    assert crawler.rate_limiter is not None
    acquire = AsyncMock()

    async with crawler:
        assert crawler.session is not None
        assert crawler.session.headers["User-Agent"] == "MyBot"
        with (
            patch.object(crawler.rate_limiter, "acquire", acquire),
            patch.object(
                crawler.session,
                "get",
                side_effect=[
                    ResponseContext(robots),
                    ResponseContext("<html>allowed</html>"),
                ],
            ) as get_mock,
        ):
            result = await crawler.fetch_url(url)

    assert result == "<html>allowed</html>"
    assert get_mock.call_args_list == [
        call("https://example.com/robots.txt"),
        call(url),
    ]
    assert acquire.await_args_list == [
        call("example.com", min_interval=0.5),
        call("example.com", min_interval=2.0),
    ]


@pytest.mark.asyncio
async def test_robots_blocked_url_is_not_requested() -> None:
    url = "https://example.com/private/page"
    crawler = AsyncCrawler(respect_robots=True, user_agent="MyBot")
    assert crawler.rate_limiter is not None
    acquire = AsyncMock()

    async with crawler:
        assert crawler.session is not None
        with (
            patch.object(crawler.rate_limiter, "acquire", acquire),
            patch.object(
                crawler.session,
                "get",
                return_value=ResponseContext(
                    "User-agent: MyBot\nDisallow: /private/"
                ),
            ) as get_mock,
        ):
            result = await crawler.fetch_url(url)

    assert result == f"Error: Blocked by robots.txt for {url}"
    assert crawler.blocked_urls == {url: "Blocked by robots.txt"}
    assert get_mock.call_args_list == [call("https://example.com/robots.txt")]
    acquire.assert_awaited_once_with("example.com", min_interval=0.0)


@pytest.mark.asyncio
async def test_crawl_counts_blocked_page_separately_from_failures() -> None:
    url = "https://example.com/private/page"
    crawler = AsyncCrawler(respect_robots=True, user_agent="MyBot")
    assert crawler.rate_limiter is not None

    async with crawler:
        assert crawler.session is not None
        with (
            patch.object(crawler.rate_limiter, "acquire", AsyncMock()),
            patch.object(
                crawler.session,
                "get",
                return_value=ResponseContext(
                    "User-agent: MyBot\nDisallow: /private/"
                ),
            ),
        ):
            results = await crawler.crawl([url], max_depth=0)

    stats = crawler.get_crawl_stats()
    assert results == {}
    assert crawler.failed_urls == {}
    assert crawler.blocked_urls == {url: "Blocked by robots.txt"}
    assert stats["pages_failed"] == 0
    assert stats["pages_blocked"] == 1
    assert stats["pages_completed"] == 1


@pytest.mark.asyncio
async def test_rate_limiter_is_applied_without_robots_check() -> None:
    url = "https://example.com/page"
    crawler = AsyncCrawler(requests_per_second=2.0, min_delay=0.75)
    assert crawler.rate_limiter is not None
    acquire = AsyncMock()

    async with crawler:
        assert crawler.session is not None
        with (
            patch.object(crawler.rate_limiter, "acquire", acquire),
            patch.object(
                crawler.session,
                "get",
                return_value=ResponseContext("OK"),
            ),
        ):
            result = await crawler.fetch_url(url)

    assert result == "OK"
    acquire.assert_awaited_once_with("example.com", min_interval=0.75)
    assert crawler.robots_parser is None


@pytest.mark.asyncio
async def test_request_stats_include_robots_fetch_and_retry_attempts() -> None:
    url = "https://example.com/page"
    crawler = AsyncCrawler(
        respect_robots=True,
        requests_per_second=1_000_000_000.0,
        max_attempts=2,
        retry_base_delay=0.001,
    )
    crawler.retry_policy._sleep = AsyncMock()

    async with crawler:
        assert crawler.session is not None
        with patch.object(
            crawler.session,
            "get",
            side_effect=[
                ResponseContext("User-agent: *\nAllow: /"),
                ResponseContext("temporary", status=503),
                ResponseContext("recovered"),
            ],
        ):
            result = await crawler.fetch_result(url)

    stats = crawler.get_request_stats()
    assert result.content == "recovered"
    assert result.attempts == 2
    assert stats["total_requests"] == 3  # robots.txt + two page attempts
    assert stats["successful_requests"] == 2
    assert stats["http_errors"] == 1
    assert stats["failed_requests"] == 1
    assert stats["rate_limited_requests"] == 3
    assert stats["scheduled_retries"] == 1
    assert stats["robots_network_fetches"] == 1
    assert stats["robots_cache_hits"] == 1
    assert stats["robots_allowed"] == 2
