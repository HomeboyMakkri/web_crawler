"""Asynchronous request-rate limiting for the polite crawler."""

import asyncio
import math
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from numbers import Real
from typing import TypedDict


Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class RateLimiterStats(TypedDict):
    requests_per_second: float
    per_domain: bool
    min_interval: float
    total_requests: int
    delayed_requests: int
    total_wait_time: float
    average_wait_time: float
    requests_by_domain: dict[str, int]


class RateLimiter:
    """Space request starts globally or independently for each domain.

    Every call reserves a point on a monotonic timeline. Sleeping happens
    outside the state lock, so a delayed domain does not block other domains.
    """

    _GLOBAL_KEY = "__global__"

    def __init__(
        self,
        requests_per_second: float = 1.0,
        per_domain: bool = True,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._requests_per_second = self._validate_rate(requests_per_second)
        if not isinstance(per_domain, bool):
            raise ValueError("per_domain must be a boolean")
        if not callable(clock):
            raise ValueError("clock must be callable")
        if not callable(sleep):
            raise ValueError("sleep must be callable")

        self._per_domain = per_domain
        self._interval = 1.0 / self._requests_per_second
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._last_scheduled_at: dict[str, float] = {}

        self._total_requests = 0
        self._delayed_requests = 0
        self._total_wait_time = 0.0
        self._requests_by_key: defaultdict[str, int] = defaultdict(int)

    async def acquire(
        self,
        domain: str | None = None,
        *,
        min_interval: float = 0.0,
    ) -> None:
        """Wait until the next request slot for ``domain`` is available."""
        key = self._get_key(domain)
        required_interval = max(
            self._interval,
            self._validate_min_interval(min_interval),
        )

        async with self._lock:
            now = self._clock()
            last_scheduled_at = self._last_scheduled_at.get(key)
            scheduled_at = (
                now
                if last_scheduled_at is None
                else max(now, last_scheduled_at + required_interval)
            )
            wait_time = max(0.0, scheduled_at - now)

            # Reserve the following slot before releasing the lock. Concurrent
            # callers therefore cannot receive the same point in time.
            self._last_scheduled_at[key] = scheduled_at
            self._total_requests += 1
            self._requests_by_key[key] += 1
            self._total_wait_time += wait_time
            if wait_time > 0:
                self._delayed_requests += 1

        if wait_time > 0:
            await self._sleep(wait_time)

    def get_stats(self) -> RateLimiterStats:
        """Return a snapshot of limiter configuration and waiting statistics."""
        average_wait = (
            self._total_wait_time / self._total_requests
            if self._total_requests
            else 0.0
        )
        requests_by_key = dict(self._requests_by_key)

        return {
            "requests_per_second": self._requests_per_second,
            "per_domain": self._per_domain,
            "min_interval": self._interval,
            "total_requests": self._total_requests,
            "delayed_requests": self._delayed_requests,
            "total_wait_time": self._total_wait_time,
            "average_wait_time": average_wait,
            "requests_by_domain": (
                requests_by_key
                if self._per_domain
                else {"global": requests_by_key.get(self._GLOBAL_KEY, 0)}
            ),
        }

    def _get_key(self, domain: str | None) -> str:
        if not self._per_domain:
            return self._GLOBAL_KEY
        if not isinstance(domain, str) or not domain.strip():
            raise ValueError("domain must be a non-empty string in per-domain mode")
        normalized_domain = domain.strip().lower().rstrip(".")
        if not normalized_domain:
            raise ValueError("domain must be a non-empty string in per-domain mode")
        return normalized_domain

    @staticmethod
    def _validate_rate(value: float) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError("requests_per_second must be a positive finite number")
        return float(value)

    @staticmethod
    def _validate_min_interval(value: float) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError("min_interval must be a non-negative finite number")
        return float(value)
