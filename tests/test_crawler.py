import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from src.crawler import AsyncCrawler


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

    async def __aenter__(self):
        await asyncio.sleep(self._delay)
        if self._enter_exception is not None:
            raise self._enter_exception
        return self._response

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None


@pytest.mark.asyncio
async def test_fetch_valid_url(caplog: pytest.LogCaptureFixture) -> None:
    url = "https://example.com/page"
    expected_body = "<html>Hello World</html>"
    caplog.set_level(logging.INFO, logger="src.crawler")

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
    ],
)
def test_invalid_configuration(kwargs: dict, parameter_name: str) -> None:
    with pytest.raises(ValueError, match=parameter_name):
        AsyncCrawler(**kwargs)
