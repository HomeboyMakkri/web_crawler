"""Asynchronous loading, caching and querying of robots.txt rules."""

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from numbers import Real
from typing import TypedDict
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import aiohttp

from .fetch_result import FetchOutcome, FetchResult
from .http_transport import HttpTransport


logger = logging.getLogger(__name__)

RobotsFetcher = Callable[[str], Awaitable[FetchResult]]


class RobotsFetchResult(TypedDict):
    origin: str
    robots_url: str
    status: int | None
    available: bool
    error: str | None
    from_cache: bool


class RobotsStats(TypedDict):
    cached_origins: int
    network_fetches: int
    cache_hits: int
    allowed_checks: int
    blocked_checks: int


@dataclass(slots=True)
class _CachedRules:
    origin: str
    robots_url: str
    parser: RobotFileParser
    status: int | None
    available: bool
    error: str | None


class RobotsParser:
    """Fetch and cache robots.txt rules independently for each origin.

    Missing files and network failures use a fail-open policy, while explicit
    HTTP 401/403 responses block crawling for that origin.
    """

    def __init__(
        self,
        *,
        user_agent: str = "AsyncCrawler/1.0",
        timeout: float = 10.0,
        fetcher: RobotsFetcher | None = None,
    ) -> None:
        self._user_agent = self._validate_user_agent(user_agent)
        self._timeout = self._validate_timeout(timeout)
        if fetcher is not None and not callable(fetcher):
            raise ValueError("fetcher must be callable")

        self._fetcher = fetcher
        self._owned_transport: HttpTransport | None = None
        self._cache: dict[str, _CachedRules] = {}
        self._origin_locks: dict[str, asyncio.Lock] = {}

        self._network_fetches = 0
        self._cache_hits = 0
        self._allowed_checks = 0
        self._blocked_checks = 0

    async def fetch_robots(self, base_url: str) -> RobotsFetchResult:
        """Load and cache the robots.txt rules for ``base_url``'s origin."""
        origin = self.get_origin(base_url)
        cached = self._cache.get(origin)
        if cached is not None:
            self._cache_hits += 1
            return self._as_result(cached, from_cache=True)

        # Creating and retrieving a lock has no await point, so concurrent
        # coroutines in this event loop receive the same lock for one origin.
        lock = self._origin_locks.setdefault(origin, asyncio.Lock())
        async with lock:
            # Another coroutine may have populated the cache while this one
            # was waiting for the origin lock.
            cached = self._cache.get(origin)
            if cached is not None:
                self._cache_hits += 1
                return self._as_result(cached, from_cache=True)

            rules = await self._load_rules(origin)
            self._cache[origin] = rules
            return self._as_result(rules, from_cache=False)

    def can_fetch(self, url: str, user_agent: str = "*") -> bool:
        """Return whether cached rules allow ``user_agent`` to fetch ``url``."""
        origin = self.get_origin(url)
        rules = self._get_cached_rules(origin)
        normalized_user_agent = self._validate_user_agent(user_agent)
        allowed = rules.parser.can_fetch(normalized_user_agent, url)

        if allowed:
            self._allowed_checks += 1
        else:
            self._blocked_checks += 1
        return allowed

    def get_crawl_delay(
        self,
        user_agent: str = "*",
        *,
        base_url: str | None = None,
    ) -> float:
        """Return the cached Crawl-delay, or zero when it is not specified.

        ``base_url`` may be omitted when rules for exactly one origin have
        been loaded. It is required once the parser contains multiple origins.
        """
        normalized_user_agent = self._validate_user_agent(user_agent)
        rules = self._resolve_rules(base_url)
        delay = rules.parser.crawl_delay(normalized_user_agent)
        return float(delay) if delay is not None else 0.0

    def get_stats(self) -> RobotsStats:
        """Return cache and permission-check counters."""
        return {
            "cached_origins": len(self._cache),
            "network_fetches": self._network_fetches,
            "cache_hits": self._cache_hits,
            "allowed_checks": self._allowed_checks,
            "blocked_checks": self._blocked_checks,
        }

    @property
    def cached_origins(self) -> frozenset[str]:
        return frozenset(self._cache)

    async def close(self) -> None:
        """Close the internally owned transport, if it was created."""
        if self._owned_transport is not None:
            await self._owned_transport.close()
        self._owned_transport = None

    async def __aenter__(self) -> "RobotsParser":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.close()

    async def _load_rules(self, origin: str) -> _CachedRules:
        robots_url = f"{origin}/robots.txt"
        parser = RobotFileParser(robots_url)
        self._network_fetches += 1

        try:
            result = await self._fetch(robots_url)
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            error_message = f"{type(error).__name__}: {error}"
            logger.warning("Could not load %s: %s", robots_url, error_message)
            parser.parse([])
            return _CachedRules(
                origin=origin,
                robots_url=robots_url,
                parser=parser,
                status=None,
                available=False,
                error=error_message,
            )

        if result.outcome in {
            FetchOutcome.NETWORK_ERROR,
            FetchOutcome.TIMEOUT,
            FetchOutcome.ROBOTS_BLOCKED,
        }:
            error_message = result.error or result.outcome.value
            logger.warning("Could not load %s: %s", robots_url, error_message)
            parser.parse([])
            return _CachedRules(
                origin=origin,
                robots_url=robots_url,
                parser=parser,
                status=None,
                available=False,
                error=error_message,
            )

        status = result.status_code
        if status is None:
            raise RuntimeError("HTTP robots result must contain status_code")
        content = result.content or ""

        if 200 <= status < 300:
            parser.parse(content.splitlines())
            available = True
            error_message = None
        elif status in {401, 403}:
            # An explicit refusal is treated conservatively: no page may be
            # fetched from this origin.
            parser.parse(["User-agent: *", "Disallow: /"])
            available = False
            error_message = f"HTTP {status}"
        else:
            # A missing robots.txt means that the site published no rules.
            parser.parse([])
            available = False
            error_message = f"HTTP {status}"

        return _CachedRules(
            origin=origin,
            robots_url=robots_url,
            parser=parser,
            status=status,
            available=available,
            error=error_message,
        )

    async def _fetch(self, robots_url: str) -> FetchResult:
        if self._fetcher is not None:
            return await self._fetcher(robots_url)

        if self._owned_transport is None:
            self._owned_transport = HttpTransport(
                max_concurrent=2,
                connect_timeout=self._timeout,
                read_timeout=self._timeout,
                limit_per_host=1,
                user_agent=self._user_agent,
            )
        return await self._owned_transport.fetch(robots_url)

    def _resolve_rules(self, base_url: str | None) -> _CachedRules:
        if base_url is not None:
            return self._get_cached_rules(self.get_origin(base_url))
        if not self._cache:
            raise RuntimeError("robots.txt rules have not been fetched")
        if len(self._cache) > 1:
            raise ValueError("base_url is required when multiple origins are cached")
        return next(iter(self._cache.values()))

    def _get_cached_rules(self, origin: str) -> _CachedRules:
        try:
            return self._cache[origin]
        except KeyError as error:
            raise RuntimeError(
                f"robots.txt rules have not been fetched for {origin}"
            ) from error

    @staticmethod
    def _as_result(rules: _CachedRules, *, from_cache: bool) -> RobotsFetchResult:
        return {
            "origin": rules.origin,
            "robots_url": rules.robots_url,
            "status": rules.status,
            "available": rules.available,
            "error": rules.error,
            "from_cache": from_cache,
        }

    @staticmethod
    def get_origin(url: str) -> str:
        """Normalize an HTTP(S) URL to its scheme, host and optional port."""
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url must be a non-empty HTTP(S) URL")

        parsed = urlsplit(url.strip())
        if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("url must be a valid HTTP(S) URL")

        scheme = parsed.scheme.lower()
        host = parsed.hostname.lower()
        host_for_url = f"[{host}]" if ":" in host else host
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("url must contain a valid port") from error

        default_port = 80 if scheme == "http" else 443
        port_suffix = f":{port}" if port is not None and port != default_port else ""
        return f"{scheme}://{host_for_url}{port_suffix}"

    @staticmethod
    def _validate_user_agent(user_agent: str) -> str:
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise ValueError("user_agent must be a non-empty string")
        return user_agent.strip()

    @staticmethod
    def _validate_timeout(timeout: float) -> float:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, Real)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be a positive finite number")
        return float(timeout)
