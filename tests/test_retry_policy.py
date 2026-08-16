from unittest.mock import AsyncMock, call

import pytest

from src.fetch_result import FetchResult
from src.retry_policy import RetryPolicy


URL = "https://example.com/page"


@pytest.mark.parametrize(
    "result",
    [
        FetchResult.timeout(URL),
        FetchResult.network_error(URL, "ConnectionError"),
        FetchResult.http_error(URL, 429),
        FetchResult.http_error(URL, 500),
        FetchResult.http_error(URL, 503),
    ],
)
def test_retryable_failures_are_retried(result: FetchResult) -> None:
    policy = RetryPolicy(max_attempts=3)

    assert policy.should_retry(result, attempt=1) is True


@pytest.mark.parametrize(
    "result",
    [
        FetchResult.success(URL, "OK"),
        FetchResult.http_error(URL, 400),
        FetchResult.http_error(URL, 404),
        FetchResult.blocked(URL),
    ],
)
def test_non_retryable_results_are_not_retried(result: FetchResult) -> None:
    policy = RetryPolicy(max_attempts=3)

    assert policy.should_retry(result, attempt=1) is False


def test_last_allowed_attempt_is_not_retried() -> None:
    policy = RetryPolicy(max_attempts=3)
    result = FetchResult.timeout(URL)

    assert policy.should_retry(result, attempt=2) is True
    assert policy.should_retry(result, attempt=3) is False


def test_exponential_delays_are_capped() -> None:
    policy = RetryPolicy(max_attempts=6, base_delay=0.5, max_delay=2.0)

    assert [policy.calculate_delay(attempt) for attempt in range(1, 7)] == [
        0.5,
        1.0,
        2.0,
        2.0,
        2.0,
        2.0,
    ]


@pytest.mark.asyncio
async def test_wait_before_retry_sleeps_and_updates_stats() -> None:
    sleep = AsyncMock()
    policy = RetryPolicy(
        max_attempts=4,
        base_delay=0.25,
        max_delay=2.0,
        sleep=sleep,
    )
    result = FetchResult.network_error(URL, "ConnectionError")

    first_scheduled = await policy.wait_before_retry(result, attempt=1)
    second_scheduled = await policy.wait_before_retry(result, attempt=2)

    assert first_scheduled is True
    assert second_scheduled is True
    assert sleep.await_args_list == [call(0.25), call(0.5)]
    assert policy.get_stats() == {
        "scheduled_retries": 2,
        "total_backoff_time": 0.75,
    }


@pytest.mark.asyncio
async def test_wait_does_nothing_for_non_retryable_result() -> None:
    sleep = AsyncMock()
    policy = RetryPolicy(sleep=sleep)

    scheduled = await policy.wait_before_retry(
        FetchResult.http_error(URL, 404),
        attempt=1,
    )

    assert scheduled is False
    sleep.assert_not_awaited()
    assert policy.get_stats() == {
        "scheduled_retries": 0,
        "total_backoff_time": 0.0,
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_attempts": 0}, "max_attempts"),
        ({"max_attempts": True}, "max_attempts"),
        ({"base_delay": 0}, "base_delay"),
        ({"base_delay": float("nan")}, "base_delay"),
        ({"max_delay": 0}, "max_delay"),
        (
            {"base_delay": 2.0, "max_delay": 1.0},
            "max_delay must be greater than or equal to base_delay",
        ),
        ({"sleep": None}, "sleep must be callable"),
    ],
)
def test_invalid_configuration_is_rejected(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        RetryPolicy(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("attempt", [0, -1, True, 4])
def test_invalid_attempt_is_rejected(attempt: object) -> None:
    policy = RetryPolicy(max_attempts=3)

    with pytest.raises(ValueError, match="attempt"):
        policy.should_retry(
            FetchResult.timeout(URL),
            attempt,  # type: ignore[arg-type]
        )


def test_invalid_result_is_rejected() -> None:
    policy = RetryPolicy()

    with pytest.raises(ValueError, match="result must be a FetchResult"):
        policy.should_retry("timeout", attempt=1)  # type: ignore[arg-type]
