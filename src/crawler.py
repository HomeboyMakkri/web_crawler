"""Basic asynchronous HTTP crawler used in day 1 of the project."""

import asyncio
import logging
import time
from collections.abc import Callable
from numbers import Real
from typing import Any

import aiohttp

from .crawl_reporter import CrawlReporter
from .crawler_queue import CrawlerQueue
from .error_tracker import ErrorTracker
from .errors import ParseError
from .fetch_result import FetchOutcome, FetchResult
from .html_parser import HTMLParser
from .http_transport import HttpTransport
from .politeness_manager import PolitenessManager
from .rate_limiter import RateLimiter
from .request_executor import RequestExecutor
from .retry_strategy import RetryStrategy
from .robots_parser import RobotsParser
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
        total_timeout: float = 30.0,
        timeout_multiplier: float = 2.0,
        max_timeout: float = 120.0,
        limit_per_host: int | None = None,
        filter_external_links: bool = False,
        max_depth: int = 2,
        requests_per_second: float | None = None,
        respect_robots: bool = False,
        min_delay: float = 0.0,
        jitter: float = 0.0,
        user_agent: str = "AsyncCrawler/1.0",
        max_attempts: int = 1,
        retry_base_delay: float = 0.5,
        retry_max_delay: float = 30.0,
    ) -> None:
        self._transport = HttpTransport(
            max_concurrent=max_concurrent,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            total_timeout=total_timeout,
            timeout_multiplier=timeout_multiplier,
            max_timeout=max_timeout,
            limit_per_host=limit_per_host,
            user_agent=user_agent,
        )
        self._max_concurrent = max_concurrent
        self._max_depth = self._validate_non_negative_int(max_depth, "max_depth")
        self._parser = HTMLParser(filter_external_links=filter_external_links)
        self._politeness = PolitenessManager(
            fetcher=self._transport.fetch,
            requests_per_second=requests_per_second,
            respect_robots=respect_robots,
            min_delay=min_delay,
            jitter=jitter,
            user_agent=user_agent,
        )
        validated_max_attempts = self._validate_positive_int(
            max_attempts,
            "max_attempts",
        )
        self._retry_strategy = RetryStrategy(
            max_retries=validated_max_attempts - 1,
            base_delay=retry_base_delay,
            max_delay=retry_max_delay,
        )
        self._request_executor = RequestExecutor(
            fetcher=self._transport.fetch,
            prepare_request=self._politeness.prepare_request,
            retry_strategy=self._retry_strategy,
        )
        self._error_tracker = ErrorTracker()

        self.visited_urls: set[str] = set()
        self.processed_urls: dict[str, dict[str, Any]] = {}
        self.failed_urls: dict[str, str] = {}
        self.blocked_urls: dict[str, str] = {}
        self.url_depths: dict[str, int] = {}
        self._crawl_queue: CrawlerQueue | None = None
        self._crawl_running = False
        self._crawl_started_at: float | None = None
        self._crawl_finished_at: float | None = None

    @property
    def session(self) -> aiohttp.ClientSession | None:
        """Expose the current session for inspection and testing."""
        return self._transport.session

    @property
    def semaphore_manager(self) -> SemaphoreManager:
        """Expose read-only access to request concurrency statistics."""
        return self._transport.semaphore_manager

    @property
    def rate_limiter(self) -> RateLimiter | None:
        """Compatibility view of the limiter now owned by politeness policy."""
        return self._politeness.rate_limiter

    @property
    def robots_parser(self) -> RobotsParser | None:
        """Compatibility view of the parser now owned by politeness policy."""
        return self._politeness.robots_parser

    @property
    def politeness_manager(self) -> PolitenessManager:
        """Expose the request-policy component for inspection and testing."""
        return self._politeness

    @property
    def retry_strategy(self) -> RetryStrategy:
        """Expose retry configuration and statistics."""
        return self._retry_strategy

    @property
    def error_tracker(self) -> ErrorTracker:
        """Expose the component owning terminal errors and their statistics."""
        return self._error_tracker

    @property
    def final_errors(self) -> dict[str, dict[str, object]]:
        """Compatibility view of records now owned by ErrorTracker."""
        return self._error_tracker.final_errors

    @property
    def request_executor(self) -> RequestExecutor:
        """Expose the component coordinating policy, transport and retries."""
        return self._request_executor

    def _get_session(self) -> aiohttp.ClientSession:
        """Compatibility adapter for tests and earlier project stages."""
        return self._transport.get_session()

    async def fetch_url(self, url: str) -> str:
        """Day 1 adapter returning either page content or a readable error."""
        result = await self.fetch_result(url)
        return self._to_legacy_fetch_value(result)

    async def fetch_result(self, url: str) -> FetchResult:
        """Fetch one URL using the typed internal request contract."""
        result = await self._request_executor.fetch(url)
        if result.outcome is FetchOutcome.ROBOTS_BLOCKED:
            self.blocked_urls[url] = result.error or "Blocked by robots.txt"
        self._error_tracker.record_fetch_result(result)
        return result

    async def fetch_urls(self, urls: list[str]) -> dict[str, str]:
        """Load all URLs concurrently, bounded by ``max_concurrent``."""
        results = await asyncio.gather(*(self.fetch_url(url) for url in urls))
        return dict(zip(urls, results, strict=True))

    def get_request_stats(self) -> dict[str, object]:
        """Aggregate HTTP, politeness and retry statistics."""
        transport = self._transport.get_stats()
        politeness = self._politeness.get_stats()
        errors = self._error_tracker.get_stats(
            self._retry_strategy.get_stats(),
        )
        return {
            "total_requests": transport["total_requests"],
            "successful_requests": transport["successful_requests"],
            "failed_requests": transport["failed_requests"],
            "http_errors": transport["http_errors"],
            "network_errors": transport["network_errors"],
            "timeouts": transport["timeouts"],
            "current_requests_per_second": transport[
                "current_requests_per_second"
            ],
            "average_request_time": transport["average_request_time"],
            "rate_limited_requests": politeness["rate_limited_requests"],
            "delayed_requests": politeness["delayed_requests"],
            "total_rate_limit_wait": politeness["total_rate_limit_wait"],
            "average_rate_limit_wait": politeness["average_rate_limit_wait"],
            "scheduled_retries": errors["scheduled_retries"],
            "total_backoff_time": errors["total_backoff_time"],
            "errors_by_type": errors["errors_by_type"],
            "successful_retries": errors["successful_retries"],
            "average_retry_wait": errors["average_retry_wait"],
            "permanent_error_urls": errors["permanent_error_urls"],
            "robots_network_fetches": politeness["robots_network_fetches"],
            "robots_cache_hits": politeness["robots_cache_hits"],
            "robots_allowed": politeness["robots_allowed"],
            "robots_blocked": politeness["robots_blocked"],
        }

    def get_error_stats(self) -> dict[str, object]:
        """Return retry counters and the latest final failures by URL."""
        return self._error_tracker.get_stats(
            self._retry_strategy.get_stats(),
        )

    async def fetch_and_parse(self, url: str) -> dict[str, Any]:
        """Load one URL and return its structured parsed representation."""
        fetch_result = await self.fetch_result(url)
        if not fetch_result.is_success:
            error = self._to_legacy_fetch_value(fetch_result)
            logger.warning("Skipping HTML parsing for %s: %s", url, error)
            return self._parser.empty_result(url, error=error)

        html = fetch_result.content
        if html is None:
            raise RuntimeError("successful FetchResult must contain HTML content")

        try:
            result = await self._parser.parse_html(html, url)
        except ParseError as error:
            self._error_tracker.record_parse_error(error)
            raise
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
                    request_stats_provider=self.get_request_stats,
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
                "blocked": 0,
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
            "pages_blocked": queue_stats["blocked"],
            "pages_completed": completed,
            "active_requests": self._transport.semaphore_manager.active_total,
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
            task = await queue._wait_for_next_task()
            self.visited_urls.add(task.url)
            self.url_depths[task.url] = task.depth

            try:
                result = await self.fetch_and_parse(task.url)

                fetch_error = result.get("error")
                if fetch_error:
                    error_message = str(fetch_error)
                    if task.url in self.blocked_urls:
                        queue.mark_blocked(task.url, self.blocked_urls[task.url])
                    else:
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
        self.blocked_urls.clear()
        self._error_tracker.clear_final_errors()
        self.url_depths.clear()
        self._crawl_queue = None
        self._crawl_started_at = None
        self._crawl_finished_at = None

    async def close(self) -> None:
        """Close the shared HTTP session; calling this twice is safe."""
        await self._politeness.close()
        await self._transport.close()

    async def __aenter__(self) -> "AsyncCrawler":
        await self._transport.__aenter__()
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

    @staticmethod
    def _to_legacy_fetch_value(result: FetchResult) -> str:
        """Keep the original Day 1 string API at the public boundary."""
        if result.outcome is FetchOutcome.SUCCESS:
            if result.content is None:
                raise RuntimeError("successful FetchResult must contain content")
            return result.content
        if result.outcome is FetchOutcome.HTTP_ERROR:
            return f"Error: HTTP {result.status_code}"
        if result.outcome is FetchOutcome.TIMEOUT:
            return f"Error: Timeout for {result.url}"
        if result.outcome is FetchOutcome.ROBOTS_BLOCKED:
            return f"Error: {result.error} for {result.url}"

        error_type = (result.error or "ClientError").partition(":")[0]
        return f"Error: ClientError {error_type}"
