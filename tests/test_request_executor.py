from unittest.mock import AsyncMock, call

import pytest

from src.fetch_result import FetchOutcome, FetchResult
from src.request_executor import RequestExecutor
from src.retry_strategy import RetryStrategy


URL = "https://example.com/page"


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


@pytest.mark.asyncio
async def test_success_is_returned_after_one_attempt() -> None:
    prepare_request = AsyncMock(return_value=None)
    fetcher = AsyncMock(return_value=FetchResult.success(URL, "OK"))
    executor = RequestExecutor(
        fetcher=fetcher,
        prepare_request=prepare_request,
        retry_strategy=RetryStrategy(max_retries=2),
        clock=lambda: 1.0,
    )

    result = await executor.fetch(URL)

    assert result.is_success
    assert result.content == "OK"
    assert result.attempts == 1
    assert result.elapsed_seconds == 0.0
    prepare_request.assert_awaited_once_with(URL)
    fetcher.assert_awaited_once_with(URL)


@pytest.mark.asyncio
async def test_retryable_failures_are_retried_until_success() -> None:
    fake_time = FakeTime()
    prepare_request = AsyncMock(return_value=None)
    fetcher = AsyncMock(
        side_effect=[
            FetchResult.timeout(URL),
            FetchResult.http_error(URL, 503),
            FetchResult.success(URL, "recovered"),
        ],
    )
    strategy = RetryStrategy(
        max_retries=3,
        base_delay=0.5,
        max_delay=4.0,
        sleep=fake_time.sleep,
    )
    executor = RequestExecutor(
        fetcher=fetcher,
        prepare_request=prepare_request,
        retry_strategy=strategy,
        clock=fake_time.clock,
    )

    result = await executor.fetch(URL)

    assert result.outcome is FetchOutcome.SUCCESS
    assert result.content == "recovered"
    assert result.attempts == 3
    assert result.elapsed_seconds == pytest.approx(1.5)
    assert fake_time.sleeps == [0.5, 1.0]
    assert prepare_request.await_args_list == [call(URL), call(URL), call(URL)]
    assert fetcher.await_count == 3
    stats = strategy.get_stats()
    assert stats["scheduled_retries"] == 2
    assert stats["successful_retries"] == 1
    assert stats["total_backoff_time"] == 1.5
    assert stats["errors_by_type"] == {"TransientError": 2}


@pytest.mark.asyncio
async def test_final_failure_is_returned_after_attempt_limit() -> None:
    sleep = AsyncMock()
    fetcher = AsyncMock(return_value=FetchResult.timeout(URL))
    strategy = RetryStrategy(
        max_retries=2,
        base_delay=0.25,
        sleep=sleep,
    )
    executor = RequestExecutor(
        fetcher=fetcher,
        prepare_request=AsyncMock(return_value=None),
        retry_strategy=strategy,
    )

    result = await executor.fetch(URL)

    assert result.outcome is FetchOutcome.TIMEOUT
    assert result.attempts == 3
    assert fetcher.await_count == 3
    assert sleep.await_args_list == [call(0.25), call(0.5)]


@pytest.mark.asyncio
async def test_non_retryable_failure_stops_immediately() -> None:
    sleep = AsyncMock()
    fetcher = AsyncMock(return_value=FetchResult.http_error(URL, 404))
    strategy = RetryStrategy(max_retries=2, sleep=sleep)
    executor = RequestExecutor(
        fetcher=fetcher,
        prepare_request=AsyncMock(return_value=None),
        retry_strategy=strategy,
    )

    result = await executor.fetch(URL)

    assert result.outcome is FetchOutcome.HTTP_ERROR
    assert result.status_code == 404
    assert result.attempts == 1
    fetcher.assert_awaited_once_with(URL)
    sleep.assert_not_awaited()
    stats = strategy.get_stats()
    assert stats["errors_by_type"] == {"PermanentError": 1}
    assert stats["non_retryable_failures"] == 1
    assert stats["permanent_error_urls"] == [URL]


@pytest.mark.asyncio
async def test_network_failure_is_classified_and_retried() -> None:
    sleep = AsyncMock()
    prepare_request = AsyncMock(return_value=None)
    fetcher = AsyncMock(
        side_effect=[
            FetchResult.network_error(URL, "DNS lookup failed"),
            FetchResult.success(URL, "recovered"),
        ],
    )
    strategy = RetryStrategy(max_retries=1, sleep=sleep)
    executor = RequestExecutor(
        fetcher=fetcher,
        prepare_request=prepare_request,
        retry_strategy=strategy,
    )

    result = await executor.fetch(URL)

    assert result.is_success
    assert result.attempts == 2
    assert prepare_request.await_args_list == [call(URL), call(URL)]
    assert strategy.get_stats()["errors_by_type"] == {"NetworkError": 1}


@pytest.mark.asyncio
async def test_policy_rejection_never_reaches_transport() -> None:
    blocked = FetchResult.blocked(URL)
    prepare_request = AsyncMock(return_value=blocked)
    fetcher = AsyncMock()
    executor = RequestExecutor(
        fetcher=fetcher,
        prepare_request=prepare_request,
        retry_strategy=RetryStrategy(max_retries=2),
    )

    result = await executor.fetch(URL)

    assert result.outcome is FetchOutcome.ROBOTS_BLOCKED
    assert result.attempts == 1
    prepare_request.assert_awaited_once_with(URL)
    fetcher.assert_not_awaited()


@pytest.mark.asyncio
async def test_policy_is_checked_again_before_every_retry() -> None:
    blocked = FetchResult.blocked(URL)
    prepare_request = AsyncMock(side_effect=[None, blocked])
    fetcher = AsyncMock(return_value=FetchResult.timeout(URL))
    strategy = RetryStrategy(
        max_retries=2,
        base_delay=0.1,
        sleep=AsyncMock(),
    )
    executor = RequestExecutor(
        fetcher=fetcher,
        prepare_request=prepare_request,
        retry_strategy=strategy,
    )

    result = await executor.fetch(URL)

    assert result.outcome is FetchOutcome.ROBOTS_BLOCKED
    assert result.attempts == 1
    assert prepare_request.await_count == 2
    fetcher.assert_awaited_once_with(URL)
    assert strategy.get_stats()["scheduled_retries"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["fetcher", "prepare_request"])
async def test_invalid_dependency_result_is_rejected(source: str) -> None:
    fetcher = AsyncMock(return_value=FetchResult.success(URL, "OK"))
    prepare_request = AsyncMock(return_value=None)
    if source == "fetcher":
        fetcher.return_value = "OK"
    else:
        prepare_request.return_value = "blocked"

    executor = RequestExecutor(
        fetcher=fetcher,
        prepare_request=prepare_request,
        retry_strategy=RetryStrategy(max_retries=0),
    )

    with pytest.raises(RuntimeError, match=f"{source} must return FetchResult"):
        await executor.fetch(URL)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"fetcher": None}, "fetcher must be callable"),
        ({"prepare_request": None}, "prepare_request must be callable"),
        (
            {"retry_strategy": "retry"},
            "retry_strategy must be a RetryStrategy",
        ),
        ({"clock": None}, "clock must be callable"),
    ],
)
def test_invalid_configuration_is_rejected(
    kwargs: dict[str, object],
    message: str,
) -> None:
    arguments: dict[str, object] = {
        "fetcher": AsyncMock(),
        "prepare_request": AsyncMock(),
        **kwargs,
    }

    with pytest.raises(ValueError, match=message):
        RequestExecutor(**arguments)  # type: ignore[arg-type]
