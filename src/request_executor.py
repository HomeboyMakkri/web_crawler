"""Orchestrate request policy, HTTP attempts and typed retries."""

import time
from collections.abc import Awaitable, Callable
from dataclasses import replace

from .errors import CrawlerError, classify_fetch_result
from .fetch_result import FetchResult
from .retry_strategy import RetryStrategy

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
        retry_strategy: RetryStrategy | None = None,
        clock: Clock = time.perf_counter,
    ) -> None:
        if not callable(fetcher):
            raise ValueError("fetcher must be callable")
        if not callable(prepare_request):
            raise ValueError("prepare_request must be callable")
        if retry_strategy is not None and not isinstance(
            retry_strategy,
            RetryStrategy,
        ):
            raise ValueError("retry_strategy must be a RetryStrategy")
        if not callable(clock):
            raise ValueError("clock must be callable")

        self._fetcher = fetcher
        self._prepare_request = prepare_request
        self._retry_strategy = retry_strategy or RetryStrategy()
        self._clock = clock

    @property
    def retry_strategy(self) -> RetryStrategy:
        return self._retry_strategy

    async def fetch(self, url: str) -> FetchResult:
        """Execute ``url`` until success, terminal failure or retry exhaustion."""
        started_at = self._clock()
        attempts_made = 0

        async def execute_attempt() -> FetchResult:
            nonlocal attempts_made

            policy_result = await self._prepare_request(url)
            if policy_result is not None:
                self._validate_result(policy_result, "prepare_request")
                return policy_result

            result = await self._fetcher(url)
            self._validate_result(result, "fetcher")
            attempts_made += 1

            error = classify_fetch_result(result)
            if error is not None:
                raise error
            return result

        try:
            result = await self._retry_strategy.execute_with_retry(execute_attempt)
        except CrawlerError as error:
            if error.fetch_result is None:
                raise RuntimeError(
                    "classified fetch error must contain FetchResult",
                ) from error
            result = error.fetch_result

        return self._finalize(result, max(1, attempts_made), started_at)

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
