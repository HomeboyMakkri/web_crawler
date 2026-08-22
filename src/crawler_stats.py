"""Pure construction of detached crawler run-statistics snapshots."""

from collections import Counter
from collections.abc import Mapping
from typing import TypedDict, cast
from urllib.parse import urlsplit

from .fetch_result import FetchResult


class TopDomainStats(TypedDict):
    domain: str
    pages: int


class RequestStatsSnapshot(TypedDict):
    total_requests: int
    successful_requests: int
    failed_requests: int
    http_errors: int
    network_errors: int
    timeouts: int
    current_requests_per_second: float
    average_request_time: float
    rate_limited_requests: int
    delayed_requests: int
    total_rate_limit_wait: float
    average_rate_limit_wait: float
    scheduled_retries: int
    total_backoff_time: float
    errors_by_type: dict[str, int]
    successful_retries: int
    average_retry_wait: float
    permanent_error_urls: list[str]
    robots_network_fetches: int
    robots_cache_hits: int
    robots_allowed: int
    robots_blocked: int


class RunStats(TypedDict):
    total_pages: int
    pages_completed: int
    successful: int
    failed: int
    blocked: int
    pages_scheduled: int
    pages_queued: int
    active_tasks: int
    active_requests: int
    max_depth_reached: int
    total_text_length: int
    total_links: int
    total_images: int
    elapsed_seconds: float
    pages_per_second: float
    status_codes: dict[str, int]
    top_domains: list[TopDomainStats]
    request_stats: dict[str, object]
    storage_stats: dict[str, object] | None


class CrawlerStats:
    """Build run snapshots without owning mutable event counters."""

    def build_snapshot(
        self,
        *,
        crawl_stats: Mapping[str, int | float],
        elapsed_seconds: float,
        page_outcomes: Mapping[str, FetchResult | None],
        request_stats: RequestStatsSnapshot,
        request_stats_baseline: RequestStatsSnapshot | None,
        final_errors: Mapping[str, Mapping[str, object]],
        storage_stats: Mapping[str, object] | None,
        storage_stats_baseline: Mapping[str, object] | None,
    ) -> RunStats:
        """Return a new canonical snapshot derived only from supplied state."""
        total_pages = int(crawl_stats["pages_completed"])
        elapsed = float(elapsed_seconds)

        return {
            "total_pages": total_pages,
            "pages_completed": total_pages,
            "successful": int(crawl_stats["pages_successful"]),
            "failed": int(crawl_stats["pages_failed"]),
            "blocked": int(crawl_stats["pages_blocked"]),
            "pages_scheduled": int(crawl_stats["pages_scheduled"]),
            "pages_queued": int(crawl_stats["pages_queued"]),
            "active_tasks": int(crawl_stats["pages_active"]),
            "active_requests": int(crawl_stats["active_requests"]),
            "max_depth_reached": int(crawl_stats["max_depth_reached"]),
            "total_text_length": int(crawl_stats["total_text_length"]),
            "total_links": int(crawl_stats["total_links"]),
            "total_images": int(crawl_stats["total_images"]),
            "elapsed_seconds": elapsed,
            "pages_per_second": total_pages / elapsed if elapsed else 0.0,
            "status_codes": self.status_code_distribution(page_outcomes),
            "top_domains": self.top_domains(page_outcomes),
            "request_stats": self.request_stats_delta(
                request_stats,
                request_stats_baseline,
                final_errors=final_errors,
            ),
            "storage_stats": self.storage_stats_delta(
                storage_stats,
                storage_stats_baseline,
            ),
        }

    @staticmethod
    def status_code_distribution(
        page_outcomes: Mapping[str, FetchResult | None],
    ) -> dict[str, int]:
        """Count final page statuses in deterministic numeric order."""
        counts = Counter(
            outcome.status_code
            for outcome in page_outcomes.values()
            if outcome is not None and outcome.status_code is not None
        )
        return {
            str(status_code): counts[status_code]
            for status_code in sorted(counts)
        }

    @staticmethod
    def top_domains(
        page_outcomes: Mapping[str, FetchResult | None],
    ) -> list[TopDomainStats]:
        """Return at most ten terminal-page host counts in stable order."""
        counts: Counter[str] = Counter()
        for url in page_outcomes:
            try:
                hostname = urlsplit(url).hostname
            except ValueError:
                hostname = None
            if hostname is not None:
                counts[hostname.lower()] += 1

        ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return [
            {"domain": domain, "pages": pages}
            for domain, pages in ordered[:10]
        ]

    @staticmethod
    def request_stats_delta(
        current: RequestStatsSnapshot,
        baseline: RequestStatsSnapshot | None,
        *,
        final_errors: Mapping[str, Mapping[str, object]],
    ) -> dict[str, object]:
        """Derive one run's request metrics from cumulative component data."""
        previous = baseline or current

        def count_delta(value: int, prior: int) -> int:
            return max(0, value - prior)

        def number_delta(value: float, prior: float) -> float:
            return max(0.0, value - prior)

        successful_requests = count_delta(
            current["successful_requests"],
            previous["successful_requests"],
        )
        failed_requests = count_delta(
            current["failed_requests"],
            previous["failed_requests"],
        )
        completed_requests = successful_requests + failed_requests
        current_completed = (
            current["successful_requests"] + current["failed_requests"]
        )
        previous_completed = (
            previous["successful_requests"] + previous["failed_requests"]
        )
        request_time = max(
            0.0,
            current["average_request_time"] * current_completed
            - previous["average_request_time"] * previous_completed,
        )

        rate_limited_requests = count_delta(
            current["rate_limited_requests"],
            previous["rate_limited_requests"],
        )
        total_rate_limit_wait = number_delta(
            current["total_rate_limit_wait"],
            previous["total_rate_limit_wait"],
        )
        scheduled_retries = count_delta(
            current["scheduled_retries"],
            previous["scheduled_retries"],
        )
        total_backoff_time = number_delta(
            current["total_backoff_time"],
            previous["total_backoff_time"],
        )

        current_errors = current["errors_by_type"]
        previous_errors = previous["errors_by_type"]
        errors_by_type = {
            error_name: delta
            for error_name in sorted(current_errors)
            if (
                delta := max(
                    0,
                    current_errors[error_name]
                    - previous_errors.get(error_name, 0),
                )
            )
        }

        permanent_error_urls = set(current["permanent_error_urls"]) - set(
            previous["permanent_error_urls"]
        )
        permanent_error_urls.update(
            url
            for url, record in final_errors.items()
            if record.get("error_type") in {"PermanentError", "ParseError"}
        )

        return {
            "total_requests": count_delta(
                current["total_requests"],
                previous["total_requests"],
            ),
            "successful_requests": successful_requests,
            "failed_requests": failed_requests,
            "http_errors": count_delta(
                current["http_errors"],
                previous["http_errors"],
            ),
            "network_errors": count_delta(
                current["network_errors"],
                previous["network_errors"],
            ),
            "timeouts": count_delta(
                current["timeouts"],
                previous["timeouts"],
            ),
            "current_requests_per_second": (
                current["current_requests_per_second"]
                if baseline is not None
                else 0.0
            ),
            "average_request_time": (
                request_time / completed_requests if completed_requests else 0.0
            ),
            "rate_limited_requests": rate_limited_requests,
            "delayed_requests": count_delta(
                current["delayed_requests"],
                previous["delayed_requests"],
            ),
            "total_rate_limit_wait": total_rate_limit_wait,
            "average_rate_limit_wait": (
                total_rate_limit_wait / rate_limited_requests
                if rate_limited_requests
                else 0.0
            ),
            "scheduled_retries": scheduled_retries,
            "total_backoff_time": total_backoff_time,
            "errors_by_type": errors_by_type,
            "successful_retries": count_delta(
                current["successful_retries"],
                previous["successful_retries"],
            ),
            "average_retry_wait": (
                total_backoff_time / scheduled_retries
                if scheduled_retries
                else 0.0
            ),
            "permanent_error_urls": sorted(permanent_error_urls),
            "robots_network_fetches": count_delta(
                current["robots_network_fetches"],
                previous["robots_network_fetches"],
            ),
            "robots_cache_hits": count_delta(
                current["robots_cache_hits"],
                previous["robots_cache_hits"],
            ),
            "robots_allowed": count_delta(
                current["robots_allowed"],
                previous["robots_allowed"],
            ),
            "robots_blocked": count_delta(
                current["robots_blocked"],
                previous["robots_blocked"],
            ),
        }

    @classmethod
    def storage_stats_delta(
        cls,
        current: Mapping[str, object] | None,
        baseline: Mapping[str, object] | None,
    ) -> dict[str, object] | None:
        """Subtract nested cumulative integer storage counters."""
        if current is None:
            return None
        previous = baseline if baseline is not None else current
        return cls._subtract_numeric_stats(current, previous)

    @classmethod
    def _subtract_numeric_stats(
        cls,
        current: Mapping[str, object],
        baseline: Mapping[str, object],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in current.items():
            baseline_value = baseline.get(name)
            if isinstance(value, Mapping):
                nested_baseline = (
                    cast(Mapping[str, object], baseline_value)
                    if isinstance(baseline_value, Mapping)
                    else {}
                )
                result[name] = cls._subtract_numeric_stats(
                    cast(Mapping[str, object], value),
                    nested_baseline,
                )
            elif isinstance(value, int) and not isinstance(value, bool):
                previous = (
                    baseline_value
                    if isinstance(baseline_value, int)
                    and not isinstance(baseline_value, bool)
                    else 0
                )
                result[name] = max(0, value - previous)
            else:
                raise RuntimeError("storage statistics must contain integers")
        return result
