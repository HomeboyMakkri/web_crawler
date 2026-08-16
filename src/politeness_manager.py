"""Request politeness policy: rate limiting and robots.txt compliance."""

import logging
import math
import random
from collections.abc import Awaitable, Callable
from numbers import Real

from .fetch_result import FetchResult
from .rate_limiter import RateLimiter
from .robots_parser import RobotsParser
from .semaphore_manager import SemaphoreManager


logger = logging.getLogger(__name__)

Fetcher = Callable[[str], Awaitable[FetchResult]]
RandomSource = Callable[[], float]


class PolitenessManager:
    """Decide whether and when an HTTP request may start.

    The manager combines the crawler's two request policies: spacing request
    starts with ``RateLimiter`` and respecting cached ``robots.txt`` rules.
    Actual HTTP I/O remains delegated to the injected ``fetcher``.
    """

    def __init__(
        self,
        *,
        fetcher: Fetcher,
        requests_per_second: float | None = None,
        respect_robots: bool = False,
        min_delay: float = 0.0,
        jitter: float = 0.0,
        user_agent: str = "AsyncCrawler/1.0",
        random_source: RandomSource = random.random,
    ) -> None:
        if not callable(fetcher):
            raise ValueError("fetcher must be callable")
        if not isinstance(respect_robots, bool):
            raise ValueError("respect_robots must be a boolean")

        self._fetcher = fetcher
        self._min_delay = self._validate_non_negative_number(
            min_delay,
            "min_delay",
        )
        self._jitter = self._validate_non_negative_number(jitter, "jitter")
        self._user_agent = self._validate_non_empty_string(
            user_agent,
            "user_agent",
        )
        if not callable(random_source):
            raise ValueError("random_source must be callable")
        self._random_source = random_source

        rate_limiting_enabled = (
            requests_per_second is not None
            or respect_robots
            or self._min_delay > 0
            or self._jitter > 0
        )
        self._rate_limiter = (
            RateLimiter(
                requests_per_second=(
                    1.0 if requests_per_second is None else requests_per_second
                ),
                per_domain=True,
            )
            if rate_limiting_enabled
            else None
        )
        self._robots_parser = (
            RobotsParser(
                user_agent=self._user_agent,
                fetcher=self._fetch_robots_document,
            )
            if respect_robots
            else None
        )

    @property
    def rate_limiter(self) -> RateLimiter | None:
        return self._rate_limiter

    @property
    def robots_parser(self) -> RobotsParser | None:
        return self._robots_parser

    async def prepare_request(self, url: str) -> FetchResult | None:
        """Wait until ``url`` may be fetched or return a policy rejection."""
        crawl_delay = 0.0
        if self._robots_parser is not None:
            await self._robots_parser.fetch_robots(url)
            if not self._robots_parser.can_fetch(url, self._user_agent):
                logger.warning("Blocked by robots.txt: %s", url)
                return FetchResult.blocked(url)
            crawl_delay = self._robots_parser.get_crawl_delay(
                self._user_agent,
                base_url=url,
            )

        await self._acquire_rate_slot(url, extra_delay=crawl_delay)
        return None

    async def close(self) -> None:
        """Close resources owned by the robots parser, if any."""
        if self._robots_parser is not None:
            await self._robots_parser.close()

    async def _acquire_rate_slot(
        self,
        url: str,
        *,
        extra_delay: float = 0.0,
    ) -> None:
        if self._rate_limiter is None:
            return
        domain = SemaphoreManager.get_domain(url)
        required_interval = max(self._min_delay, extra_delay)
        if self._jitter > 0:
            required_interval += self._get_jitter_delay()
        await self._rate_limiter.acquire(
            domain,
            min_interval=required_interval,
        )

    async def _fetch_robots_document(self, url: str) -> FetchResult:
        """Fetch robots.txt without recursively checking robots.txt itself."""
        await self._acquire_rate_slot(url)
        return await self._fetcher(url)

    def _get_jitter_delay(self) -> float:
        random_fraction = self._random_source()
        if (
            isinstance(random_fraction, bool)
            or not isinstance(random_fraction, Real)
            or not math.isfinite(random_fraction)
            or not 0.0 <= random_fraction <= 1.0
        ):
            raise ValueError("random_source must return a finite number from 0 to 1")
        return float(random_fraction) * self._jitter

    @staticmethod
    def _validate_non_negative_number(value: float, name: str) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(f"{name} must be a non-negative finite number")
        return float(value)

    @staticmethod
    def _validate_non_empty_string(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value.strip()
