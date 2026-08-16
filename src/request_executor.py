"""Orchestrate request policy, HTTP attempts and retries."""

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace

from .fetch_result import FetchResult
from .retry_policy import RetryPolicy


logger = logging.getLogger(__name__)

Fetcher = Callable[[str], Awaitable[FetchResult]]
RequestPreparer = Callable[[str], Awaitable[FetchResult | None]]
Clock = Callable[[], float]


class RequestExecutor:
    """Execute one logical request, which may contain several HTTP attempts."""

    def __init__(
        self,
        *,
        fetcher: Fetcher,
        prepare_request: RequestPreparer,
        retry_policy: RetryPolicy | None = None,
        clock: Clock = time.perf_counter,
    ) -> None:
        if not callable(fetcher):
            raise ValueError("fetcher must be callable")
        if not callable(prepare_request):
            raise ValueError("prepare_request must be callable")
        if retry_policy is not None and not isinstance(retry_policy, RetryPolicy):
            raise ValueError("retry_policy must be a RetryPolicy")
        if not callable(clock):
            raise ValueError("clock must be callable")

        self._fetcher = fetcher
        self._prepare_request = prepare_request
        self._retry_policy = retry_policy or RetryPolicy()
        self._clock = clock

    @property
    def retry_policy(self) -> RetryPolicy:
        return self._retry_policy

    async def fetch(self, url: str) -> FetchResult:
        """Execute ``url`` until success, terminal failure or retry exhaustion."""
        started_at = self._clock()
        attempts_made = 0

        for attempt in range(1, self._retry_policy.max_attempts + 1):
            policy_result = await self._prepare_request(url)
            if policy_result is not None:
                self._validate_result(policy_result, "prepare_request")
                return self._finalize(
                    policy_result,
                    max(1, attempts_made),
                    started_at,
                )

            result = await self._fetcher(url)
            self._validate_result(result, "fetcher")
            attempts_made = attempt

            if not self._retry_policy.should_retry(result, attempt):
                return self._finalize(result, attempts_made, started_at)

            delay = self._retry_policy.calculate_delay(attempt)
            logger.warning(
                "Retrying URL after failure: %s (attempt=%d, delay=%.3fs)",
                url,
                attempt,
                delay,
            )
            await self._retry_policy.wait_before_retry(result, attempt)

        # The loop always returns on the final attempt because should_retry()
        # becomes false, so reaching this branch indicates a broken contract.
        raise RuntimeError("request executor exhausted attempts without a result")

    def _finalize(
        self,
        result: FetchResult,
        attempts: int,
        started_at: float,
    ) -> FetchResult:
        elapsed = max(0.0, self._clock() - started_at)
        return replace(
            result,
            attempts=attempts,
            elapsed_seconds=elapsed,
        )

    @staticmethod
    def _validate_result(result: object, source: str) -> None:
        if not isinstance(result, FetchResult):
            raise RuntimeError(f"{source} must return FetchResult")
