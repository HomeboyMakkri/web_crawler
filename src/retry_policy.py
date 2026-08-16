"""Retry decisions and exponential-backoff delays for failed requests."""

import asyncio
import math
from collections.abc import Awaitable, Callable
from numbers import Real
from typing import TypedDict

from .fetch_result import FetchResult


Sleep = Callable[[float], Awaitable[None]]


class RetryStats(TypedDict):
    scheduled_retries: int
    total_backoff_time: float


class RetryPolicy:
    """Decide whether a failed request should be attempted again.

    ``attempt`` is the one-based number of the attempt that just completed.
    For example, a failure on attempt 1 waits ``base_delay`` before attempt 2.
    """

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 30.0,
        *,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._max_attempts = self._validate_positive_int(
            max_attempts,
            "max_attempts",
        )
        self._base_delay = self._validate_positive_number(
            base_delay,
            "base_delay",
        )
        self._max_delay = self._validate_positive_number(
            max_delay,
            "max_delay",
        )
        if self._max_delay < self._base_delay:
            raise ValueError("max_delay must be greater than or equal to base_delay")
        if not callable(sleep):
            raise ValueError("sleep must be callable")
        self._sleep = sleep

        self._scheduled_retries = 0
        self._total_backoff_time = 0.0

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    def should_retry(self, result: FetchResult, attempt: int) -> bool:
        """Return whether ``result`` may be retried after ``attempt``."""
        self._validate_result(result)
        self._validate_attempt(attempt)
        return attempt < self._max_attempts and result.is_retryable

    def calculate_delay(self, attempt: int) -> float:
        """Return capped exponential delay after the failed ``attempt``."""
        self._validate_attempt(attempt)
        exponent = attempt - 1

        # Compare logarithms before exponentiation to avoid overflowing when
        # a caller configures an unusually large max_attempts value.
        if math.log2(self._base_delay) + exponent >= math.log2(self._max_delay):
            return self._max_delay
        return min(math.ldexp(self._base_delay, exponent), self._max_delay)

    async def wait_before_retry(
        self,
        result: FetchResult,
        attempt: int,
    ) -> bool:
        """Wait before the next attempt and report whether it was scheduled."""
        if not self.should_retry(result, attempt):
            return False

        delay = self.calculate_delay(attempt)
        self._scheduled_retries += 1
        self._total_backoff_time += delay
        await self._sleep(delay)
        return True

    def get_stats(self) -> RetryStats:
        """Return retry counters accumulated by this policy instance."""
        return {
            "scheduled_retries": self._scheduled_retries,
            "total_backoff_time": self._total_backoff_time,
        }

    def _validate_attempt(self, attempt: int) -> int:
        validated = self._validate_positive_int(attempt, "attempt")
        if validated > self._max_attempts:
            raise ValueError("attempt cannot exceed max_attempts")
        return validated

    @staticmethod
    def _validate_result(result: FetchResult) -> None:
        if not isinstance(result, FetchResult):
            raise ValueError("result must be a FetchResult")

    @staticmethod
    def _validate_positive_int(value: int, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _validate_positive_number(value: float, name: str) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{name} must be a positive finite number")
        return float(value)
