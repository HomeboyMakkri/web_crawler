import asyncio
import logging
import time
from collections.abc import Callable
from types import TracebackType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from src.crawler import AsyncCrawler
from src.fetch_result import FetchOutcome


class MockResponseContext:
    """Stand-in for the async context manager returned by session.get()."""

    def __init__(
        self,
        *,
        body: str = "OK",
        status: int = 200,
        delay: float = 0.0,
        enter_exception: BaseException | None = None,
        response_exception: BaseException | None = None,
    ) -> None:
        self._delay = delay
        self._enter_exception = enter_exception
        self._response = MagicMock(status=status)
        self._response.raise_for_status = MagicMock(side_effect=response_exception)
        self._response.text = AsyncMock(return_value=body)

    async def __aenter__(self) -> Any:
        await asyncio.sleep(self._delay)
        if self._enter_exception is not None:
            raise self._enter_exception
        return self._response

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class BlockingResponseContext:
    """Keep a mocked HTTP request active until a test releases it."""

    def __init__(self, release: asyncio.Event) -> None:
        self._release = release
        self._response = MagicMock(status=200)
        self._response.raise_for_status = MagicMock()
        self._response.text = AsyncMock(return_value="OK")

    async def __aenter__(self) -> Any:
        await self._release.wait()
        return self._response

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


async def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 0.2,
) -> None:
    """Yield to crawler tasks until they reach an expected state."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_fetch_valid_url(caplog: pytest.LogCaptureFixture) -> None:
    url = "https://example.com/page"
    expected_body = "<html>Hello World</html>"
    caplog.set_level(logging.INFO, logger="src.http_transport")

    async with AsyncCrawler() as crawler:
        assert crawler.session is not None
        with patch.object(
            crawler.session,
            "get",
            return_value=MockResponseContext(body=expected_body),
        ):
            result = await crawler.fetch_url(url)

    assert result == expected_body
    assert f"Fetching URL: {url}" in caplog.text
    assert f"Successfully loaded: {url}" in caplog.text


@pytest.mark.asyncio
async def test_fetch_result_exposes_typed_internal_contract() -> None:
    url = "https://example.com/page"

    async with AsyncCrawler() as crawler:
        assert crawler.session is not None
        with patch.object(
            crawler.session,
            "get",
            return_value=MockResponseContext(body="typed content"),
        ):
            result = await crawler.fetch_result(url)

    assert result.outcome is FetchOutcome.SUCCESS
    assert result.content == "typed content"
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_fetch_http_error() -> None:
    url = "https://example.com/missing"
    error = aiohttp.ClientResponseError(
        request_info=MagicMock(),
        history=(),
        status=404,
        message="Not Found",
    )

    async with AsyncCrawler() as crawler:
        assert crawler.session is not None
        with patch.object(
            crawler.session,
            "get",
            return_value=MockResponseContext(
                status=404,
                response_exception=error,
            ),
        ):
            result = await crawler.fetch_url(url)

    assert result == "Error: HTTP 404"


@pytest.mark.asyncio
async def test_fetch_network_error() -> None:
    url = "https://does-not-exist.invalid"

    async with AsyncCrawler() as crawler:
        assert crawler.session is not None
        with patch.object(
            crawler.session,
            "get",
            return_value=MockResponseContext(
                enter_exception=aiohttp.ClientConnectionError("Connection failed")
            ),
        ):
            result = await crawler.fetch_url(url)

    assert result == "Error: ClientError ClientConnectionError"


@pytest.mark.asyncio
async def test_fetch_timeout() -> None:
    url = "https://example.com/slow"

    async with AsyncCrawler() as crawler:
        assert crawler.session is not None
        with patch.object(
            crawler.session,
            "get",
            return_value=MockResponseContext(
                enter_exception=asyncio.TimeoutError()
            ),
        ):
            result = await crawler.fetch_url(url)

    assert result == f"Error: Timeout for {url}"


async def measure_fetch_time(urls: list[str], max_concurrent: int) -> float:
    async with AsyncCrawler(max_concurrent=max_concurrent) as crawler:
        assert crawler.session is not None
        with patch.object(
            crawler.session,
            "get",
            side_effect=lambda *args, **kwargs: MockResponseContext(delay=0.05),
        ):
            started_at = time.perf_counter()
            results = await crawler.fetch_urls(urls)
            duration = time.perf_counter() - started_at

    assert results == {url: "OK" for url in urls}
    return duration


@pytest.mark.asyncio
async def test_concurrency_speed_comparison() -> None:
    urls = [f"https://example.com/item/{index}" for index in range(6)]

    sequential_time = await measure_fetch_time(urls, max_concurrent=1)
    concurrent_time = await measure_fetch_time(urls, max_concurrent=6)

    # Sequential time is about 0.30 s and concurrent time about 0.05 s.
    # A generous ratio keeps the test stable on a busy machine.
    assert concurrent_time < sequential_time / 2


@pytest.mark.asyncio
async def test_fetch_url_uses_global_semaphore_limit() -> None:
    release = asyncio.Event()
    urls = [
        "https://one.example/page",
        "https://two.example/page",
        "https://three.example/page",
    ]

    async with AsyncCrawler(max_concurrent=2, limit_per_host=2) as crawler:
        assert crawler.session is not None
        with patch.object(
            crawler.session,
            "get",
            side_effect=lambda *args, **kwargs: BlockingResponseContext(release),
        ):
            requests = [asyncio.create_task(crawler.fetch_url(url)) for url in urls]
            await wait_until(
                lambda: crawler.semaphore_manager.active_total == 2
                and crawler.semaphore_manager.get_stats()["waiting_global"] == 1
            )

            assert crawler.semaphore_manager.active_by_domain == {
                "one.example": 1,
                "two.example": 1,
            }

            release.set()
            results = await asyncio.gather(*requests)

    assert results == ["OK", "OK", "OK"]
    assert crawler.semaphore_manager.active_total == 0


@pytest.mark.asyncio
async def test_fetch_url_uses_per_domain_semaphore_limit() -> None:
    release = asyncio.Event()
    urls = [f"https://example.com/page/{index}" for index in range(3)]

    async with AsyncCrawler(max_concurrent=3, limit_per_host=1) as crawler:
        assert crawler.session is not None
        with patch.object(
            crawler.session,
            "get",
            side_effect=lambda *args, **kwargs: BlockingResponseContext(release),
        ):
            requests = [asyncio.create_task(crawler.fetch_url(url)) for url in urls]
            await wait_until(
                lambda: crawler.semaphore_manager.active_total == 1
                and crawler.semaphore_manager.get_stats()["waiting_by_domain"]
                == {"example.com": 2}
            )

            assert crawler.semaphore_manager.active_by_domain == {"example.com": 1}
            assert crawler.semaphore_manager.get_stats()["waiting_global"] == 0

            release.set()
            results = await asyncio.gather(*requests)

    assert results == ["OK", "OK", "OK"]
    assert crawler.semaphore_manager.active_by_domain == {}


@pytest.mark.asyncio
async def test_session_is_reused_and_closed() -> None:
    crawler = AsyncCrawler()

    async with crawler:
        session = crawler.session
        assert session is not None
        assert not session.closed
        assert crawler._get_session() is session

    assert session.closed
    assert crawler.session is None
    await crawler.close()  # Closing an already closed crawler is harmless.


@pytest.mark.parametrize(
    ("kwargs", "parameter_name"),
    [
        ({"max_concurrent": 0}, "max_concurrent"),
        ({"max_concurrent": True}, "max_concurrent"),
        ({"connect_timeout": 0}, "connect_timeout"),
        ({"read_timeout": -1}, "read_timeout"),
        ({"limit_per_host": 0}, "limit_per_host"),
        ({"requests_per_second": 0}, "requests_per_second"),
        ({"respect_robots": "yes"}, "respect_robots"),
        ({"min_delay": -1}, "min_delay"),
        ({"min_delay": float("nan")}, "min_delay"),
        ({"user_agent": ""}, "user_agent"),
    ],
)
def test_invalid_configuration(kwargs: dict, parameter_name: str) -> None:
    with pytest.raises(ValueError, match=parameter_name):
        AsyncCrawler(**kwargs)


@pytest.mark.asyncio
async def test_fetch_and_parse_returns_expected_structure() -> None:
    url = "https://example.com/page"
    html_content = """
        <html>
          <head>
            <title>Test</title>
            <meta name="description" content="Integration test">
          </head>
          <body>
            <h1>Hello</h1>
            <a href="/next">Next</a>
          </body>
        </html>
    """

    async with AsyncCrawler(filter_external_links=True) as crawler:
        assert crawler.session is not None
        with patch.object(
            crawler.session,
            "get",
            return_value=MockResponseContext(body=html_content),
        ):
            result = await crawler.fetch_and_parse(url)

    assert result["url"] == url
    assert result["title"] == "Test"
    assert result["text"] == "Hello Next"
    assert result["links"] == ["https://example.com/next"]
    assert result["metadata"]["description"] == "Integration test"
    assert result["headings"] == [{"level": "h1", "text": "Hello"}]
    assert result["error"] is None


@pytest.mark.asyncio
async def test_fetch_and_parse_returns_structured_fetch_error() -> None:
    url = "https://example.com/unavailable"

    async with AsyncCrawler() as crawler:
        assert crawler.session is not None
        with patch.object(
            crawler.session,
            "get",
            return_value=MockResponseContext(
                enter_exception=aiohttp.ClientConnectionError("Unavailable")
            ),
        ):
            result = await crawler.fetch_and_parse(url)

    assert result["url"] == url
    assert result["title"] == ""
    assert result["text"] == ""
    assert result["links"] == []
    assert result["error"] == "Error: ClientError ClientConnectionError"
