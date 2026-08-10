"""Basic asynchronous HTTP crawler used in day 1 of the project."""

import asyncio
import logging
from numbers import Real

import aiohttp


logger = logging.getLogger(__name__)


class AsyncCrawler:
    """Load web pages concurrently using one reusable HTTP session."""

    def __init__(
        self,
        max_concurrent: int = 10,
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 15.0,
        limit_per_host: int | None = None,
    ) -> None:
        self._max_concurrent = self._validate_positive_int(
            max_concurrent, "max_concurrent"
        )
        self._connect_timeout = self._validate_positive_number(
            connect_timeout, "connect_timeout"
        )
        self._read_timeout = self._validate_positive_number(
            read_timeout, "read_timeout"
        )
        self._limit_per_host = (
            self._max_concurrent
            if limit_per_host is None
            else self._validate_positive_int(limit_per_host, "limit_per_host")
        )

        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._session: aiohttp.ClientSession | None = None

    @property
    def session(self) -> aiohttp.ClientSession | None:
        """Expose the current session for inspection and testing."""
        return self._session

    def _get_session(self) -> aiohttp.ClientSession:
        """Create the shared session lazily and reuse it between requests."""
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
            )

        return self._session

    async def fetch_url(self, url: str) -> str:
        """Load one URL and return either its body or a readable error."""
        session = self._get_session()

        async with self._semaphore:
            logger.info("Fetching URL: %s", url)

            try:
                async with session.get(url) as response:
                    response.raise_for_status()
                    content = await response.text()
            except aiohttp.ClientResponseError as error:
                logger.warning(
                    "HTTP error for %s: %s (status=%s)",
                    url,
                    type(error).__name__,
                    error.status,
                )
                return f"Error: HTTP {error.status}"
            except asyncio.TimeoutError:
                # aiohttp.ServerTimeoutError is also a ClientError, so this
                # handler must appear before the general ClientError handler.
                logger.warning("Timeout while fetching URL: %s", url)
                return f"Error: Timeout for {url}"
            except aiohttp.ClientError as error:
                logger.warning(
                    "Network error for %s: %s (%s)",
                    url,
                    type(error).__name__,
                    error,
                )
                return f"Error: ClientError {type(error).__name__}"

            logger.info(
                "Successfully loaded: %s (status=%s, size=%d B)",
                url,
                response.status,
                len(content),
            )
            return content

    async def fetch_urls(self, urls: list[str]) -> dict[str, str]:
        """Load all URLs concurrently, bounded by ``max_concurrent``."""
        results = await asyncio.gather(*(self.fetch_url(url) for url in urls))
        return dict(zip(urls, results, strict=True))

    async def close(self) -> None:
        """Close the shared HTTP session; calling this twice is safe."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def __aenter__(self) -> "AsyncCrawler":
        self._get_session()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.close()

    @staticmethod
    def _validate_positive_int(value: int, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _validate_positive_number(value: float, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, Real) or value <= 0:
            raise ValueError(f"{name} must be a positive number")
        return float(value)
