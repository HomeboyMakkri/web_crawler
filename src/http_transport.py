"""Reusable aiohttp transport with pooling and concurrency control."""

import asyncio
import logging
import math
import time
from collections.abc import Callable
from numbers import Real

import aiohttp

from .fetch_result import FetchResult
from .semaphore_manager import SemaphoreManager


logger = logging.getLogger(__name__)

Clock = Callable[[], float]


class HttpTransport:
    """Own HTTP resources and convert expected failures to ``FetchResult``."""

    def __init__(
        self,
        max_concurrent: int = 10,
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 15.0,
        limit_per_host: int | None = None,
        user_agent: str = "AsyncCrawler/1.0",
        clock: Clock = time.perf_counter,
    ) -> None:
        self._max_concurrent = self._validate_positive_int(
            max_concurrent,
            "max_concurrent",
        )
        self._connect_timeout = self._validate_positive_number(
            connect_timeout,
            "connect_timeout",
        )
        self._read_timeout = self._validate_positive_number(
            read_timeout,
            "read_timeout",
        )
        self._limit_per_host = (
            self._max_concurrent
            if limit_per_host is None
            else self._validate_positive_int(limit_per_host, "limit_per_host")
        )
        self._user_agent = self._validate_non_empty_string(
            user_agent,
            "user_agent",
        )
        if not callable(clock):
            raise ValueError("clock must be callable")
        self._clock = clock

        self._semaphore_manager = SemaphoreManager(
            global_limit=self._max_concurrent,
            per_domain_limit=self._limit_per_host,
        )
        self._session: aiohttp.ClientSession | None = None

    @property
    def session(self) -> aiohttp.ClientSession | None:
        return self._session

    @property
    def semaphore_manager(self) -> SemaphoreManager:
        return self._semaphore_manager

    async def fetch(self, url: str) -> FetchResult:
        """Fetch one HTTP(S) URL using the shared session and connection pool."""
        # Validate before allocating a session or entering timing statistics.
        self._semaphore_manager.get_domain(url)
        session = self.get_session()
        started_at = self._clock()

        async with self._semaphore_manager.request_slot(url):
            logger.info("Fetching URL: %s", url)
            try:
                async with session.get(url) as response:
                    content = await response.text()
                    status = response.status
            except asyncio.TimeoutError:
                logger.warning("Timeout while fetching URL: %s", url)
                return FetchResult.timeout(
                    url,
                    elapsed_seconds=self._elapsed_since(started_at),
                )
            except aiohttp.ClientResponseError as error:
                logger.warning("HTTP error for %s: status=%s", url, error.status)
                if error.status:
                    return FetchResult.http_error(
                        url,
                        error.status,
                        error=str(error) or f"HTTP {error.status}",
                        elapsed_seconds=self._elapsed_since(started_at),
                    )
                return FetchResult.network_error(
                    url,
                    f"{type(error).__name__}: {error}",
                    elapsed_seconds=self._elapsed_since(started_at),
                )
            except aiohttp.ClientError as error:
                logger.warning(
                    "Network error for %s: %s (%s)",
                    url,
                    type(error).__name__,
                    error,
                )
                return FetchResult.network_error(
                    url,
                    f"{type(error).__name__}: {error}",
                    elapsed_seconds=self._elapsed_since(started_at),
                )

        elapsed = self._elapsed_since(started_at)
        if status >= 400:
            logger.warning("HTTP error for %s: status=%s", url, status)
            return FetchResult.http_error(
                url,
                status,
                content=content,
                elapsed_seconds=elapsed,
            )

        logger.info(
            "Successfully loaded: %s (status=%s, size=%d B)",
            url,
            status,
            len(content),
        )
        return FetchResult.success(
            url,
            content,
            status_code=status,
            elapsed_seconds=elapsed,
        )

    def get_session(self) -> aiohttp.ClientSession:
        """Create the pooled session lazily and reuse it between requests."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                total=None,
                connect=self._connect_timeout,
                sock_read=self._read_timeout,
            )
            connector = aiohttp.TCPConnector(
                limit=self._max_concurrent,
                limit_per_host=self._limit_per_host,
                ttl_dns_cache=300,
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={"User-Agent": self._user_agent},
            )
        return self._session

    async def close(self) -> None:
        """Close the session; repeated calls are harmless."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def __aenter__(self) -> "HttpTransport":
        self.get_session()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.close()

    def _elapsed_since(self, started_at: float) -> float:
        return max(0.0, self._clock() - started_at)

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

    @staticmethod
    def _validate_non_empty_string(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value.strip()
