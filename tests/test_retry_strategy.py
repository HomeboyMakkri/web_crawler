import asyncio
import logging
import math
from unittest.mock import AsyncMock, call

import pytest

from src.errors import NetworkError, ParseError, PermanentError, TransientError
from src.retry_strategy import RetryStrategy


URL = "https://example.com/page"


def empty_stats() -> dict[str, object]:
    return {
        "total_executions": 0,
        "total_attempts": 0,
        "errors_by_type": {},
        "scheduled_retries": 0,
        "retries_by_type": {},
        "successful_retries": 0,
        "exhausted_retries": 0,
        "non_retryable_failures": 0,
        "total_backoff_time": 0.0,
        "average_backoff_time": 0.0,
        "average_retry_wait": 0.0,
        "permanent_error_urls": [],
    }


@pytest.mark.asyncio
async def test_success_returns_value_without_retry() -> None:
    sleep = AsyncMock()
    operation = AsyncMock(return_value="OK")
    strategy = RetryStrategy(sleep=sleep)

    result = await strategy.execute_with_retry(operation, 42, mode="fast")

    assert result == "OK"
    operation.assert_awaited_once_with(42, mode="fast")
    sleep.assert_not_awaited()
    assert strategy.get_stats() == {
        **empty_stats(),
        "total_executions": 1,
        "total_attempts": 1,
    }


@pytest.mark.asyncio
async def test_transient_errors_back_off_until_success(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="src.retry_strategy")
    sleep = AsyncMock()
    operation = AsyncMock(
        side_effect=[
            TransientError("HTTP 503", url=URL, status_code=503),
            TransientError("HTTP 503", url=URL, status_code=503),
            "recovered",
        ],
    )
    strategy = RetryStrategy(
        max_retries=3,
        base_delay=0.5,
        backoff_factor=2.0,
        sleep=sleep,
    )

    result = await strategy.execute_with_retry(operation, URL)

    assert result == "recovered"
    assert operation.await_args_list == [call(URL), call(URL), call(URL)]
    assert sleep.await_args_list == [call(0.5), call(1.0)]
    assert "Retry scheduled" in caplog.text
    assert "type=TransientError" in caplog.text
    assert f"url={URL}" in caplog.text
    assert "delay=0.500s" in caplog.text
    assert "Retry succeeded" in caplog.text
    assert strategy.get_stats() == {
        "total_executions": 1,
        "total_attempts": 3,
        "errors_by_type": {"TransientError": 2},
        "scheduled_retries": 2,
        "retries_by_type": {"TransientError": 2},
        "successful_retries": 1,
        "exhausted_retries": 0,
        "non_retryable_failures": 0,
        "total_backoff_time": 1.5,
        "average_backoff_time": 0.75,
        "average_retry_wait": 0.75,
        "permanent_error_urls": [],
    }


@pytest.mark.asyncio
async def test_max_retries_means_additional_attempts() -> None:
    sleep = AsyncMock()
    errors = [NetworkError("connection lost", url=URL) for _ in range(3)]
    operation = AsyncMock(side_effect=errors)
    strategy = RetryStrategy(
        max_retries=2,
        base_delay=0.25,
        sleep=sleep,
    )

    with pytest.raises(NetworkError, match="connection lost"):
        await strategy.execute_with_retry(operation)

    assert operation.await_count == 3
    assert sleep.await_args_list == [call(0.25), call(0.5)]
    stats = strategy.get_stats()
    assert stats["errors_by_type"] == {"NetworkError": 3}
    assert stats["scheduled_retries"] == 2
    assert stats["exhausted_retries"] == 1
    assert stats["successful_retries"] == 0


@pytest.mark.asyncio
async def test_permanent_error_is_not_retried_and_url_is_remembered() -> None:
    sleep = AsyncMock()
    operation = AsyncMock(
        side_effect=PermanentError("HTTP 404", url=URL, status_code=404),
    )
    strategy = RetryStrategy(sleep=sleep)

    with pytest.raises(PermanentError, match="HTTP 404"):
        await strategy.execute_with_retry(operation)

    operation.assert_awaited_once()
    sleep.assert_not_awaited()
    stats = strategy.get_stats()
    assert stats["errors_by_type"] == {"PermanentError": 1}
    assert stats["non_retryable_failures"] == 1
    assert stats["permanent_error_urls"] == [URL]


@pytest.mark.asyncio
async def test_type_specific_limits_and_factors_are_independent() -> None:
    sleep = AsyncMock()
    operation = AsyncMock(
        side_effect=[
            TransientError("HTTP 503", url=URL),
            NetworkError("DNS", url=URL),
            NetworkError("DNS", url=URL),
            "recovered",
        ],
    )
    strategy = RetryStrategy(
        max_retries=5,
        base_delay=1.0,
        retry_limits={TransientError: 1, NetworkError: 2},
        backoff_factors={TransientError: 2.0, NetworkError: 3.0},
        sleep=sleep,
    )

    result = await strategy.execute_with_retry(operation)

    assert result == "recovered"
    assert sleep.await_args_list == [call(1.0), call(1.0), call(3.0)]
    stats = strategy.get_stats()
    assert stats["retries_by_type"] == {
        "TransientError": 1,
        "NetworkError": 2,
    }
    assert stats["successful_retries"] == 1


@pytest.mark.asyncio
async def test_type_specific_limit_can_stop_before_global_limit() -> None:
    sleep = AsyncMock()
    operation = AsyncMock(
        side_effect=[TransientError("HTTP 503", url=URL) for _ in range(2)],
    )
    strategy = RetryStrategy(
        max_retries=5,
        retry_limits={TransientError: 1},
        sleep=sleep,
    )

    with pytest.raises(TransientError):
        await strategy.execute_with_retry(operation)

    assert operation.await_count == 2
    sleep.assert_awaited_once_with(0.5)
    assert strategy.get_stats()["exhausted_retries"] == 1


@pytest.mark.asyncio
async def test_custom_retry_on_can_retry_parse_error() -> None:
    sleep = AsyncMock()
    operation = AsyncMock(
        side_effect=[ParseError("temporary parser issue", url=URL), "parsed"],
    )
    strategy = RetryStrategy(
        max_retries=1,
        retry_on=[ParseError],
        sleep=sleep,
    )

    result = await strategy.execute_with_retry(operation)

    assert result == "parsed"
    sleep.assert_awaited_once_with(0.5)
    assert strategy.get_stats()["successful_retries"] == 1


@pytest.mark.asyncio
async def test_unconfigured_exception_is_never_retried() -> None:
    sleep = AsyncMock()
    operation = AsyncMock(side_effect=RuntimeError("programming error"))
    strategy = RetryStrategy(sleep=sleep)

    with pytest.raises(RuntimeError, match="programming error"):
        await strategy.execute_with_retry(operation)

    operation.assert_awaited_once()
    sleep.assert_not_awaited()
    assert strategy.get_stats()["errors_by_type"] == {"RuntimeError": 1}
    assert strategy.get_stats()["non_retryable_failures"] == 1


@pytest.mark.asyncio
async def test_backoff_is_capped_by_max_delay() -> None:
    sleep = AsyncMock()
    operation = AsyncMock(
        side_effect=[
            *[TransientError("temporary", url=URL) for _ in range(4)],
            "OK",
        ],
    )
    strategy = RetryStrategy(
        max_retries=4,
        base_delay=1.0,
        backoff_factor=10.0,
        max_delay=5.0,
        sleep=sleep,
    )

    assert await strategy.execute_with_retry(operation) == "OK"
    assert sleep.await_args_list == [call(1.0), call(5.0), call(5.0), call(5.0)]


@pytest.mark.asyncio
async def test_zero_retries_executes_once_and_reports_exhaustion() -> None:
    sleep = AsyncMock()
    operation = AsyncMock(side_effect=TransientError("temporary", url=URL))
    strategy = RetryStrategy(max_retries=0, sleep=sleep)

    with pytest.raises(TransientError):
        await strategy.execute_with_retry(operation)

    operation.assert_awaited_once()
    sleep.assert_not_awaited()
    assert strategy.get_stats()["exhausted_retries"] == 1


@pytest.mark.asyncio
async def test_cancelled_error_is_propagated_without_retry() -> None:
    sleep = AsyncMock()
    operation = AsyncMock(side_effect=asyncio.CancelledError())
    strategy = RetryStrategy(sleep=sleep)

    with pytest.raises(asyncio.CancelledError):
        await strategy.execute_with_retry(operation)

    sleep.assert_not_awaited()
    stats = strategy.get_stats()
    assert stats["total_executions"] == 1
    assert stats["total_attempts"] == 1
    assert stats["errors_by_type"] == {}


@pytest.mark.asyncio
async def test_concurrent_executions_keep_retry_limits_independent() -> None:
    both_sleeping = asyncio.Event()
    release_sleep = asyncio.Event()
    recorded_delays: list[float] = []
    calls = {"one": 0, "two": 0}

    async def controlled_sleep(delay: float) -> None:
        recorded_delays.append(delay)
        if len(recorded_delays) == 2:
            both_sleeping.set()
        await release_sleep.wait()

    async def operation(name: str) -> str:
        calls[name] += 1
        if calls[name] == 1:
            raise TransientError(
                "temporary",
                url=f"https://example.com/{name}",
            )
        return name

    strategy = RetryStrategy(max_retries=1, sleep=controlled_sleep)
    tasks = [
        asyncio.create_task(strategy.execute_with_retry(operation, "one")),
        asyncio.create_task(strategy.execute_with_retry(operation, "two")),
    ]

    await asyncio.wait_for(both_sleeping.wait(), timeout=0.2)
    in_progress_stats = strategy.get_stats()
    assert in_progress_stats["total_executions"] == 2
    assert in_progress_stats["total_attempts"] == 2
    assert in_progress_stats["scheduled_retries"] == 2

    release_sleep.set()
    results = await asyncio.gather(*tasks)

    assert results == ["one", "two"]
    assert calls == {"one": 2, "two": 2}
    assert recorded_delays == [0.5, 0.5]
    stats = strategy.get_stats()
    assert stats["total_executions"] == 2
    assert stats["total_attempts"] == 4
    assert stats["errors_by_type"] == {"TransientError": 2}
    assert stats["scheduled_retries"] == 2
    assert stats["successful_retries"] == 2
    assert stats["exhausted_retries"] == 0


@pytest.mark.asyncio
async def test_extreme_backoff_factor_does_not_overflow() -> None:
    sleep = AsyncMock()
    operation = AsyncMock(
        side_effect=[
            *[TransientError("temporary", url=URL) for _ in range(5)],
            "recovered",
        ],
    )
    strategy = RetryStrategy(
        max_retries=5,
        base_delay=1e-300,
        backoff_factor=1e100,
        max_delay=1e200,
        sleep=sleep,
    )

    result = await strategy.execute_with_retry(operation)

    delays = [arguments.args[0] for arguments in sleep.await_args_list]
    assert result == "recovered"
    assert len(delays) == 5
    assert all(math.isfinite(delay) for delay in delays)
    assert all(0 < delay <= 1e200 for delay in delays)
    assert delays == pytest.approx([1e-300, 1e-200, 1e-100, 1.0, 1e100])


@pytest.mark.asyncio
async def test_sleep_failure_is_propagated_without_another_attempt() -> None:
    operation = AsyncMock(side_effect=TransientError("temporary", url=URL))
    sleep = AsyncMock(side_effect=RuntimeError("sleep failed"))
    strategy = RetryStrategy(max_retries=3, sleep=sleep)

    with pytest.raises(RuntimeError, match="sleep failed"):
        await strategy.execute_with_retry(operation)

    operation.assert_awaited_once()
    sleep.assert_awaited_once_with(0.5)
    stats = strategy.get_stats()
    assert stats["total_executions"] == 1
    assert stats["total_attempts"] == 1
    assert stats["errors_by_type"] == {"TransientError": 1}
    assert stats["scheduled_retries"] == 1
    assert stats["total_backoff_time"] == 0.5
    assert stats["successful_retries"] == 0
    assert stats["exhausted_retries"] == 0


def test_initial_stats_are_detached() -> None:
    strategy = RetryStrategy()

    stats = strategy.get_stats()
    assert stats == empty_stats()
    stats["errors_by_type"]["Changed"] = 1
    stats["permanent_error_urls"].append(URL)

    assert strategy.get_stats() == empty_stats()


@pytest.mark.asyncio
async def test_coroutine_object_is_rejected() -> None:
    async def operation() -> str:
        return "OK"

    coroutine_object = operation()
    try:
        with pytest.raises(ValueError, match="async callable"):
            await RetryStrategy().execute_with_retry(coroutine_object)  # type: ignore[arg-type]
    finally:
        coroutine_object.close()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_retries": -1}, "max_retries"),
        ({"max_retries": True}, "max_retries"),
        ({"backoff_factor": 0.5}, "backoff_factor"),
        ({"backoff_factor": float("nan")}, "backoff_factor"),
        ({"base_delay": 0}, "base_delay"),
        ({"max_delay": 0}, "max_delay"),
        (
            {"base_delay": 2.0, "max_delay": 1.0},
            "max_delay must be greater than or equal to base_delay",
        ),
        ({"retry_on": "TransientError"}, "retry_on"),
        ({"retry_on": [BaseException]}, "Exception types"),
        ({"retry_limits": []}, "retry_limits"),
        ({"retry_limits": {PermanentError: 1}}, "included in retry_on"),
        ({"retry_limits": {TransientError: -1}}, "retry limit"),
        (
            {"max_retries": 2, "retry_limits": {TransientError: 3}},
            "cannot exceed max_retries",
        ),
        ({"backoff_factors": []}, "backoff_factors"),
        (
            {"backoff_factors": {PermanentError: 2.0}},
            "included in retry_on",
        ),
        (
            {"backoff_factors": {TransientError: 0.5}},
            "backoff factor",
        ),
        ({"sleep": None}, "sleep"),
    ],
)
def test_invalid_configuration_is_rejected(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        RetryStrategy(**kwargs)  # type: ignore[arg-type]
