"""Basic asynchronous HTTP crawler used in day 1 of the project."""

import asyncio
import logging
import time
from collections.abc import Callable
from numbers import Real
from typing import Any

import aiohttp

from .html_parser import HTMLParser
from .crawl_reporter import CrawlReporter
from .crawler_queue import CrawlerQueue
from .semaphore_manager import SemaphoreManager
from .url_filter import URLFilter


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
        filter_external_links: bool = False,
        max_depth: int = 2,
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
        self._max_depth = self._validate_non_negative_int(max_depth, "max_depth")

        self._semaphore_manager = SemaphoreManager(
            global_limit=self._max_concurrent,
            per_domain_limit=self._limit_per_host,
        )
        self._session: aiohttp.ClientSession | None = None
        self._parser = HTMLParser(filter_external_links=filter_external_links)

        self.visited_urls: set[str] = set()
        self.processed_urls: dict[str, dict[str, Any]] = {}
        self.failed_urls: dict[str, str] = {}
        self.url_depths: dict[str, int] = {}
        self._crawl_queue: CrawlerQueue | None = None
        self._crawl_running = False
        self._crawl_started_at: float | None = None
        self._crawl_finished_at: float | None = None

    @property
    def session(self) -> aiohttp.ClientSession | None:
        """Expose the current session for inspection and testing."""
        return self._session

    @property
    def semaphore_manager(self) -> SemaphoreManager:
        """Expose read-only access to request concurrency statistics."""
        return self._semaphore_manager

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

        async with self._semaphore_manager.request_slot(url):
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

    async def fetch_and_parse(self, url: str) -> dict[str, Any]:
        """Load one URL and return its structured parsed representation."""
        html = await self.fetch_url(url)
        if html.startswith("Error:"):
            logger.warning("Skipping HTML parsing for %s: %s", url, html)
            return self._parser.empty_result(url, error=html)

        result = await self._parser.parse_html(html, url)
        logger.info(
            "Successfully parsed: %s (links=%d, text=%d chars)",
            url,
            len(result["links"]),
            len(result["text"]),
        )
        return result

    async def crawl(
        self,
        start_urls: list[str],
        max_pages: int = 100,
        *,
        max_depth: int | None = None,
        same_domain_only: bool = True,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        show_progress: bool = False,
        progress_interval: float = 1.0,
        progress_output: Callable[[str], None] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Recursively crawl pages discovered from one or more starting URLs."""
        if self._crawl_running:
            raise RuntimeError("crawl is already running on this crawler")

        self._validate_positive_int(max_pages, "max_pages")
        if not isinstance(show_progress, bool):
            raise ValueError("show_progress must be a boolean")
        validated_progress_interval = self._validate_positive_number(
            progress_interval,
            "progress_interval",
        )
        if progress_output is not None and not callable(progress_output):
            raise ValueError("progress_output must be callable")
        crawl_depth = (
            self._max_depth
            if max_depth is None
            else self._validate_non_negative_int(max_depth, "max_depth")
        )
        url_filter = URLFilter.from_start_urls(
            start_urls,
            same_domain_only=same_domain_only,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
        )

        self._reset_crawl_state()
        queue = CrawlerQueue()
        self._crawl_queue = queue

        for url in start_urls:
            if queue.scheduled_count >= max_pages:
                break
            queue.add_url(url, priority=0, depth=0)

        worker_count = min(self._max_concurrent, max_pages)
        workers: list[asyncio.Task[None]] = []
        reporter_task: asyncio.Task[None] | None = None
        reporter: CrawlReporter | None = None
        self._crawl_running = True
        self._crawl_started_at = time.perf_counter()
        self._crawl_finished_at = None

        try:
            workers = [
                asyncio.create_task(
                    self._crawl_worker(
                        queue,
                        url_filter,
                        max_depth=crawl_depth,
                        max_pages=max_pages,
                    ),
                    name=f"crawler-worker-{index}",
                )
                for index in range(worker_count)
            ]
            if show_progress:
                reporter = CrawlReporter(
                    self.get_crawl_stats,
                    interval=validated_progress_interval,
                    output=progress_output,
                )
                reporter_task = asyncio.create_task(
                    reporter.run(),
                    name="crawler-progress-reporter",
                )
            await queue.join()
        finally:
            for worker in workers:
                worker.cancel()
            if workers:
                await asyncio.gather(*workers, return_exceptions=True)
            self._crawl_finished_at = time.perf_counter()
            if reporter_task is not None:
                reporter_task.cancel()
                await asyncio.gather(reporter_task, return_exceptions=True)
            if reporter is not None:
                reporter.report_once(final=True)
            self._crawl_running = False

        return dict(self.processed_urls)

    def get_crawl_stats(self) -> dict[str, int | float]:
        """Return an aggregate snapshot of the current or latest crawl run."""
        queue_stats = (
            self._crawl_queue.get_stats()
            if self._crawl_queue is not None
            else {
                "scheduled": 0,
                "queued": 0,
                "active": 0,
                "processed": 0,
                "failed": 0,
                "completed": 0,
            }
        )
        elapsed = self._get_crawl_elapsed()
        completed = queue_stats["completed"]

        return {
            "pages_scheduled": queue_stats["scheduled"],
            "pages_queued": queue_stats["queued"],
            "pages_active": queue_stats["active"],
            "pages_successful": queue_stats["processed"],
            "pages_failed": queue_stats["failed"],
            "pages_completed": completed,
            "active_requests": self._semaphore_manager.active_total,
            "max_depth_reached": max(self.url_depths.values(), default=0),
            "total_text_length": sum(
                len(str(result.get("text", "")))
                for result in self.processed_urls.values()
            ),
            "total_links": sum(
                len(result.get("links", []))
                for result in self.processed_urls.values()
            ),
            "total_images": sum(
                len(result.get("images", []))
                for result in self.processed_urls.values()
            ),
            "elapsed_seconds": round(elapsed, 3),
            "pages_per_second": round(completed / elapsed, 3) if elapsed else 0.0,
        }

    def _get_crawl_elapsed(self) -> float:
        if self._crawl_started_at is None:
            return 0.0
        finished_at = self._crawl_finished_at or time.perf_counter()
        return max(0.0, finished_at - self._crawl_started_at)

    async def _crawl_worker(
        self,
        queue: CrawlerQueue,
        url_filter: URLFilter,
        *,
        max_depth: int,
        max_pages: int,
    ) -> None:
        """Consume crawl tasks and schedule permitted discovered links."""
        while True:
            task = await queue.get_next()
            self.visited_urls.add(task.url)
            self.url_depths[task.url] = task.depth

            try:
                result = await self.fetch_and_parse(task.url)

                fetch_error = result.get("error")
                if fetch_error:
                    error_message = str(fetch_error)
                    self.failed_urls[task.url] = error_message
                    queue.mark_failed(task.url, error_message)
                    continue

                if task.depth < max_depth:
                    next_depth = task.depth + 1
                    for link in result.get("links", []):
                        if queue.scheduled_count >= max_pages:
                            break
                        if url_filter.should_crawl(link):
                            queue.add_url(
                                link,
                                priority=next_depth,
                                depth=next_depth,
                            )

                self.processed_urls[task.url] = result
            except asyncio.CancelledError:
                error = "Cancelled while processing"
                self.processed_urls.pop(task.url, None)
                self.failed_urls[task.url] = error
                queue.mark_failed(task.url, error)
                raise
            except Exception as error:
                error_message = f"{type(error).__name__}: {error}"
                logger.exception("Unexpected crawl error for %s", task.url)
                self.processed_urls.pop(task.url, None)
                self.failed_urls[task.url] = error_message
                queue.mark_failed(task.url, error_message)
                continue

            queue.mark_processed(task.url)

    def _reset_crawl_state(self) -> None:
        """Clear results before starting an independent crawl run."""
        self.visited_urls.clear()
        self.processed_urls.clear()
        self.failed_urls.clear()
        self.url_depths.clear()
        self._crawl_queue = None
        self._crawl_started_at = None
        self._crawl_finished_at = None

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

    @staticmethod
    def _validate_non_negative_int(value: int, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
        return value
