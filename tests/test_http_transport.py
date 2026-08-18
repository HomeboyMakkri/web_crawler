import asyncio
from collections.abc import Callable
from types import TracebackType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from src.fetch_result import FetchOutcome
from src.http_transport import HttpTransport
from src.request_executor import RequestExecutor
from src.retry_strategy import RetryStrategy


class ResponseContext:
    def __init__(
        self,
        *,
        body: str = "OK",
        status: int = 200,
        enter_exception: BaseException | None = None,
        release: asyncio.Event | None = None,
    ) -> None:
        self._enter_exception = enter_exception
        self._release = release
        self._response = MagicMock(status=status)
        self._response.text = AsyncMock(return_value=body)

    async def __aenter__(self) -> Any:
        if self._release is not None:
            await self._release.wait()
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


async def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 0.2,
) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_fetch_success_reuses_session_and_reports_elapsed_time() -> None:
    clock = MagicMock(side_effect=[10.0, 10.25])
    transport = HttpTransport(user_agent="TestBot/1.0", clock=clock)

    async with transport:
        session = transport.session
        assert session is not None
        assert session.headers["User-Agent"] == "TestBot/1.0"
        with patch.object(
            session,
            "get",
            return_value=ResponseContext(body="<html>OK</html>"),
        ):
            result = await transport.fetch("https://example.com/page")
        assert transport.get_session() is session

    assert result.outcome is FetchOutcome.SUCCESS
    assert result.content == "<html>OK</html>"
    assert result.status_code == 200
    assert result.elapsed_seconds == pytest.approx(0.25)
    assert session.closed
    assert transport.session is None


@pytest.mark.asyncio
async def test_http_error_retains_status_and_response_body() -> None:
    transport = HttpTransport()

    async with transport:
        assert transport.session is not None
        with patch.object(
            transport.session,
            "get",
            return_value=ResponseContext(body="Unavailable", status=503),
        ):
            result = await transport.fetch("https://example.com/unavailable")

    assert result.outcome is FetchOutcome.HTTP_ERROR
    assert result.status_code == 503
    assert result.content == "Unavailable"
    assert result.error == "HTTP 503"
    assert result.is_retryable is True


@pytest.mark.asyncio
async def test_timeout_is_returned_as_typed_result() -> None:
    transport = HttpTransport()

    async with transport:
        assert transport.session is not None
        with patch.object(
            transport.session,
            "get",
            return_value=ResponseContext(enter_exception=asyncio.TimeoutError()),
        ):
            result = await transport.fetch("https://example.com/slow")

    assert result.outcome is FetchOutcome.TIMEOUT
    assert result.status_code is None
    assert result.content is None
    assert result.is_retryable is True


@pytest.mark.asyncio
async def test_network_error_is_returned_as_typed_result() -> None:
    transport = HttpTransport()

    async with transport:
        assert transport.session is not None
        with patch.object(
            transport.session,
            "get",
            return_value=ResponseContext(
                enter_exception=aiohttp.ClientConnectionError("connection lost")
            ),
        ):
            result = await transport.fetch("https://example.com/page")

    assert result.outcome is FetchOutcome.NETWORK_ERROR
    assert "ClientConnectionError" in str(result.error)
    assert "connection lost" in str(result.error)


@pytest.mark.asyncio
async def test_stats_count_outcomes_duration_and_recent_request_rate() -> None:
    clock = MagicMock(
        side_effect=[
            0.0,
            0.1,
            0.2,
            0.4,
            0.5,
            0.8,
            0.9,
            1.3,
            1.3,
        ],
    )
    transport = HttpTransport(clock=clock)
    urls = [f"https://example.com/{index}" for index in range(4)]

    async with transport:
        assert transport.session is not None
        with patch.object(
            transport.session,
            "get",
            side_effect=[
                ResponseContext(body="OK"),
                ResponseContext(body="Unavailable", status=503),
                ResponseContext(enter_exception=asyncio.TimeoutError()),
                ResponseContext(
                    enter_exception=aiohttp.ClientConnectionError("lost")
                ),
            ],
        ):
            for url in urls:
                await transport.fetch(url)

        stats = transport.get_stats()

    assert stats == {
        "total_requests": 4,
        "successful_requests": 1,
        "failed_requests": 3,
        "http_errors": 1,
        "network_errors": 1,
        "timeouts": 1,
        "current_requests_per_second": 2.0,
        "average_request_time": pytest.approx(0.25),
    }


def test_stats_are_zero_before_first_request() -> None:
    transport = HttpTransport(clock=lambda: 10.0)

    assert transport.get_stats() == {
        "total_requests": 0,
        "successful_requests": 0,
        "failed_requests": 0,
        "http_errors": 0,
        "network_errors": 0,
        "timeouts": 0,
        "current_requests_per_second": 0.0,
        "average_request_time": 0.0,
    }


@pytest.mark.asyncio
async def test_global_concurrency_is_enforced_around_network_operation() -> None:
    release = asyncio.Event()
    transport = HttpTransport(max_concurrent=2, limit_per_host=2)
    urls = [
        "https://one.example/page",
        "https://two.example/page",
        "https://three.example/page",
    ]

    async with transport:
        assert transport.session is not None
        with patch.object(
            transport.session,
            "get",
            side_effect=lambda url, **kwargs: ResponseContext(release=release),
        ):
            tasks = [asyncio.create_task(transport.fetch(url)) for url in urls]
            await wait_until(
                lambda: transport.semaphore_manager.active_total == 2
                and transport.semaphore_manager.get_stats()["waiting_global"] == 1
            )

            release.set()
            results = await asyncio.gather(*tasks)

    assert all(result.is_success for result in results)
    assert transport.semaphore_manager.active_total == 0


@pytest.mark.asyncio
async def test_invalid_url_does_not_create_session() -> None:
    transport = HttpTransport()

    with pytest.raises(ValueError, match="HTTP"):
        await transport.fetch("relative/path")

    assert transport.session is None


@pytest.mark.asyncio
async def test_close_is_idempotent() -> None:
    transport = HttpTransport()
    session = transport.get_session()

    await transport.close()
    await transport.close()

    assert session.closed
    assert transport.session is None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_concurrent": 0}, "max_concurrent"),
        ({"connect_timeout": 0}, "connect_timeout"),
        ({"read_timeout": float("nan")}, "read_timeout"),
        ({"total_timeout": 0}, "total_timeout"),
        ({"timeout_multiplier": 0.5}, "timeout_multiplier"),
        ({"max_timeout": 0}, "max_timeout"),
        (
            {"total_timeout": 30.0, "max_timeout": 20.0},
            "max_timeout",
        ),
        ({"limit_per_host": True}, "limit_per_host"),
        ({"user_agent": ""}, "user_agent"),
        ({"clock": "not callable"}, "clock"),
    ],
)
def test_invalid_configuration_is_rejected(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        HttpTransport(**kwargs)


def test_timeout_grows_for_each_attempt_and_is_capped() -> None:
    transport = HttpTransport(
        connect_timeout=2.0,
        read_timeout=5.0,
        total_timeout=10.0,
        timeout_multiplier=2.0,
        max_timeout=25.0,
    )

    first = transport.timeout_for_attempt(1)
    second = transport.timeout_for_attempt(2)
    fourth = transport.timeout_for_attempt(4)

    assert (first.connect, first.sock_read, first.total) == (2.0, 5.0, 10.0)
    assert (second.connect, second.sock_read, second.total) == (4.0, 10.0, 20.0)
    assert (fourth.connect, fourth.sock_read, fourth.total) == (16.0, 25.0, 25.0)


@pytest.mark.parametrize("attempt", [0, -1, True, 1.5])
def test_timeout_rejects_invalid_attempt(attempt: object) -> None:
    transport = HttpTransport()

    with pytest.raises(ValueError, match="attempt"):
        transport.timeout_for_attempt(attempt)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_fetch_uses_timeout_for_specific_attempt() -> None:
    transport = HttpTransport(
        connect_timeout=2.0,
        read_timeout=4.0,
        total_timeout=8.0,
        timeout_multiplier=2.0,
        max_timeout=20.0,
    )

    async with transport:
        assert transport.session is not None
        with patch.object(
            transport.session,
            "get",
            return_value=ResponseContext(),
        ) as get_mock:
            result = await transport.fetch("https://example.com", attempt=3)

    timeout = get_mock.call_args.kwargs["timeout"]
    assert isinstance(timeout, aiohttp.ClientTimeout)
    assert (timeout.connect, timeout.sock_read, timeout.total) == (8.0, 16.0, 20.0)
    assert result.attempts == 3


@pytest.mark.asyncio
async def test_asyncio_timeout_is_classified_and_retried_with_larger_timeout() -> None:
    url = "https://example.com/slow"
    transport = HttpTransport(
        connect_timeout=1.0,
        read_timeout=2.0,
        total_timeout=3.0,
        timeout_multiplier=2.0,
        max_timeout=10.0,
    )
    strategy = RetryStrategy(max_retries=1, sleep=AsyncMock())
    executor = RequestExecutor(
        fetcher=transport.fetch,
        prepare_request=AsyncMock(return_value=None),
        retry_strategy=strategy,
    )

    async with transport:
        assert transport.session is not None
        with patch.object(
            transport.session,
            "get",
            side_effect=[
                ResponseContext(enter_exception=asyncio.TimeoutError()),
                ResponseContext(body="recovered"),
            ],
        ) as get_mock:
            result = await executor.fetch(url)

    assert result.is_success
    assert result.content == "recovered"
    assert result.attempts == 2
    assert strategy.get_stats()["errors_by_type"] == {"TransientError": 1}
    assert [
        request.kwargs["timeout"].total
        for request in get_mock.call_args_list
    ] == [3.0, 6.0]
    assert get_mock.call_args_list[0].args == (url,)
    assert get_mock.call_args_list[1].args == (url,)
