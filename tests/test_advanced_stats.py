import asyncio
from collections.abc import Iterable
from unittest.mock import AsyncMock, patch

import pytest

from src.composite_storage import CompositeStorage
from src.crawl_record import CrawlRecord
from src.crawler import AsyncCrawler
from src.data_storage import DataStorage
from src.errors import ParseError
from src.fetch_result import FetchResult


HTML = "<html><body><p>page text</p></body></html>"


def zero_request_stats() -> dict[str, object]:
    return {
        "total_requests": 0,
        "successful_requests": 0,
        "failed_requests": 0,
        "http_errors": 0,
        "network_errors": 0,
        "timeouts": 0,
        "current_requests_per_second": 0.0,
        "average_request_time": 0.0,
        "rate_limited_requests": 0,
        "delayed_requests": 0,
        "total_rate_limit_wait": 0.0,
        "average_rate_limit_wait": 0.0,
        "scheduled_retries": 0,
        "total_backoff_time": 0.0,
        "errors_by_type": {},
        "successful_retries": 0,
        "average_retry_wait": 0.0,
        "permanent_error_urls": [],
        "robots_network_fetches": 0,
        "robots_cache_hits": 0,
        "robots_allowed": 0,
        "robots_blocked": 0,
    }


async def crawl_urls(crawler: AsyncCrawler, urls: list[str]) -> None:
    await crawler.crawl(
        urls,
        max_pages=max(1, len(urls)),
        max_depth=0,
        same_domain_only=False,
    )


def test_canonical_stats_are_zero_and_detached_before_first_run() -> None:
    crawler = AsyncCrawler()

    stats = crawler.get_stats()

    assert stats == {
        "total_pages": 0,
        "pages_completed": 0,
        "successful": 0,
        "failed": 0,
        "blocked": 0,
        "pages_scheduled": 0,
        "pages_queued": 0,
        "active_tasks": 0,
        "active_requests": 0,
        "max_depth_reached": 0,
        "total_text_length": 0,
        "total_links": 0,
        "total_images": 0,
        "elapsed_seconds": 0.0,
        "pages_per_second": 0.0,
        "status_codes": {},
        "top_domains": [],
        "request_stats": zero_request_stats(),
        "storage_stats": None,
    }

    status_codes = stats["status_codes"]
    top_domains = stats["top_domains"]
    request_stats = stats["request_stats"]
    assert isinstance(status_codes, dict)
    assert isinstance(top_domains, list)
    assert isinstance(request_stats, dict)
    status_codes["999"] = 1
    top_domains.append({"domain": "changed", "pages": 1})
    request_stats["total_requests"] = 99

    fresh = crawler.get_stats()
    assert fresh["status_codes"] == {}
    assert fresh["top_domains"] == []
    assert fresh["request_stats"] == zero_request_stats()


@pytest.mark.asyncio
async def test_terminal_invariant_and_final_status_distribution() -> None:
    missing = "https://example.com/missing"
    redirected = "https://example.com/redirected"
    successful = "https://example.com/successful"
    blocked = "https://example.com/private"
    outcomes = {
        missing: FetchResult.http_error(missing, 404),
        redirected: FetchResult.success(redirected, HTML, status_code=302),
        successful: FetchResult.success(successful, HTML, status_code=200),
        blocked: FetchResult.blocked(blocked),
    }
    crawler = AsyncCrawler(max_concurrent=1)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda url: outcomes[url]
    )

    await crawl_urls(crawler, list(outcomes))
    stats = crawler.get_stats()

    assert stats["total_pages"] == 4
    assert stats["pages_completed"] == 4
    assert stats["successful"] == 2
    assert stats["failed"] == 1
    assert stats["blocked"] == 1
    assert stats["total_pages"] == (
        stats["successful"] + stats["failed"] + stats["blocked"]
    )
    assert stats["status_codes"] == {"200": 1, "302": 1, "404": 1}
    assert stats["top_domains"] == [{"domain": "example.com", "pages": 4}]
    assert stats["storage_stats"] is None
    assert stats["elapsed_seconds"] > 0
    assert stats["pages_per_second"] == pytest.approx(
        stats["total_pages"] / stats["elapsed_seconds"]
    )


@pytest.mark.asyncio
async def test_retries_count_only_the_final_page_status() -> None:
    url = "https://example.com/recovered"
    crawler = AsyncCrawler(max_concurrent=1, max_attempts=3)
    crawler.request_executor._fetcher = AsyncMock(
        side_effect=[
            FetchResult.http_error(url, 503),
            FetchResult.http_error(url, 503),
            FetchResult.success(url, HTML, status_code=201),
        ]
    )
    crawler.retry_strategy._sleep = AsyncMock()

    await crawl_urls(crawler, [url])
    stats = crawler.get_stats()

    assert stats["status_codes"] == {"201": 1}
    request_stats = stats["request_stats"]
    assert isinstance(request_stats, dict)
    assert request_stats["scheduled_retries"] == 2
    assert request_stats["successful_retries"] == 1


@pytest.mark.asyncio
async def test_parser_failure_keeps_successful_fetch_status() -> None:
    url = "https://example.com/broken-html"
    crawler = AsyncCrawler(max_concurrent=1)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=FetchResult.success(url, HTML, status_code=206)
    )
    crawler._parser.parse_html = AsyncMock(  # type: ignore[method-assign]
        side_effect=ParseError("parser failed", url=url)
    )

    await crawl_urls(crawler, [url])
    stats = crawler.get_stats()

    assert stats["successful"] == 0
    assert stats["failed"] == 1
    assert stats["status_codes"] == {"206": 1}


@pytest.mark.asyncio
async def test_no_status_failures_and_robots_are_excluded_from_distribution() -> None:
    network = "https://network.example/page"
    timeout = "https://timeout.example/page"
    blocked = "https://blocked.example/page"
    outcomes = {
        network: FetchResult.network_error(network, "connection lost"),
        timeout: FetchResult.timeout(timeout),
        blocked: FetchResult.blocked(blocked),
    }
    crawler = AsyncCrawler(max_concurrent=1)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda url: outcomes[url]
    )

    await crawl_urls(crawler, list(outcomes))
    stats = crawler.get_stats()

    assert stats["failed"] == 2
    assert stats["blocked"] == 1
    assert stats["status_codes"] == {}
    assert stats["top_domains"] == [
        {"domain": "blocked.example", "pages": 1},
        {"domain": "network.example", "pages": 1},
        {"domain": "timeout.example", "pages": 1},
    ]


@pytest.mark.asyncio
async def test_top_domains_normalize_hosts_sort_ties_and_limit_to_ten() -> None:
    urls = [
        "https://Alpha.Example:8443/one",
        "https://alpha.example/two",
        "http://Beta.Example:8080/one",
        "https://beta.example:443/two",
        *(f"https://d{index:02d}.example/page" for index in range(10)),
    ]
    crawler = AsyncCrawler(max_concurrent=1)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda url: FetchResult.success(url, HTML)
    )

    await crawl_urls(crawler, urls)
    top_domains = crawler.get_stats()["top_domains"]

    assert top_domains == [
        {"domain": "alpha.example", "pages": 2},
        {"domain": "beta.example", "pages": 2},
        *(
            {"domain": f"d{index:02d}.example", "pages": 1}
            for index in range(8)
        ),
    ]


@pytest.mark.asyncio
async def test_sequential_runs_reset_page_status_and_domain_state() -> None:
    first = "https://first.example/page"
    second = "https://second.example/page"
    outcomes = {
        first: FetchResult.success(first, HTML, status_code=202),
        second: FetchResult.http_error(second, 410),
    }
    crawler = AsyncCrawler(max_concurrent=1)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda url: outcomes[url]
    )

    await crawl_urls(crawler, [first])
    first_stats = crawler.get_stats()
    await crawl_urls(crawler, [second])
    second_stats = crawler.get_stats()

    assert first_stats["status_codes"] == {"202": 1}
    assert first_stats["top_domains"] == [
        {"domain": "first.example", "pages": 1}
    ]
    assert second_stats["total_pages"] == 1
    assert second_stats["successful"] == 0
    assert second_stats["failed"] == 1
    assert second_stats["status_codes"] == {"410": 1}
    assert second_stats["top_domains"] == [
        {"domain": "second.example", "pages": 1}
    ]


def test_request_statistics_are_per_run_deltas_of_cumulative_components() -> None:
    crawler = AsyncCrawler()
    baseline = zero_request_stats() | {
        "total_requests": 6,
        "successful_requests": 4,
        "failed_requests": 1,
        "average_request_time": 0.2,
        "rate_limited_requests": 5,
        "delayed_requests": 2,
        "total_rate_limit_wait": 1.0,
        "scheduled_retries": 1,
        "total_backoff_time": 0.5,
        "errors_by_type": {"TransientError": 1},
        "successful_retries": 1,
        "permanent_error_urls": ["https://old.example"],
        "robots_network_fetches": 1,
        "robots_cache_hits": 2,
        "robots_allowed": 3,
    }
    current = zero_request_stats() | {
        "total_requests": 10,
        "successful_requests": 6,
        "failed_requests": 2,
        "http_errors": 1,
        "network_errors": 1,
        "current_requests_per_second": 7.0,
        "average_request_time": 0.25,
        "rate_limited_requests": 8,
        "delayed_requests": 4,
        "total_rate_limit_wait": 2.5,
        "scheduled_retries": 3,
        "total_backoff_time": 2.5,
        "errors_by_type": {"TransientError": 3, "PermanentError": 1},
        "successful_retries": 2,
        "permanent_error_urls": [
            "https://old.example",
            "https://new.example",
        ],
        "robots_network_fetches": 2,
        "robots_cache_hits": 5,
        "robots_allowed": 6,
        "robots_blocked": 1,
    }
    crawler._request_stats_baseline = baseline

    with patch.object(crawler, "get_request_stats", return_value=current):
        request_stats = crawler.get_stats()["request_stats"]

    assert request_stats == {
        "total_requests": 4,
        "successful_requests": 2,
        "failed_requests": 1,
        "http_errors": 1,
        "network_errors": 1,
        "timeouts": 0,
        "current_requests_per_second": 7.0,
        "average_request_time": pytest.approx(1 / 3),
        "rate_limited_requests": 3,
        "delayed_requests": 2,
        "total_rate_limit_wait": 1.5,
        "average_rate_limit_wait": 0.5,
        "scheduled_retries": 2,
        "total_backoff_time": 2.0,
        "errors_by_type": {"PermanentError": 1, "TransientError": 2},
        "successful_retries": 1,
        "average_retry_wait": 1.0,
        "permanent_error_urls": ["https://new.example"],
        "robots_network_fetches": 1,
        "robots_cache_hits": 3,
        "robots_allowed": 3,
        "robots_blocked": 1,
    }


@pytest.mark.asyncio
async def test_repeated_component_activity_is_not_reported_cumulatively() -> None:
    url = "https://example.com/repeated"
    crawler = AsyncCrawler(max_concurrent=1, max_attempts=2)
    crawler.request_executor._fetcher = AsyncMock(
        side_effect=[
            FetchResult.http_error(url, 503),
            FetchResult.success(url, HTML),
            FetchResult.http_error(url, 503),
            FetchResult.success(url, HTML),
        ]
    )
    crawler.retry_strategy._sleep = AsyncMock()

    await crawl_urls(crawler, [url])
    first = crawler.get_stats()["request_stats"]
    await crawl_urls(crawler, [url])
    second = crawler.get_stats()["request_stats"]

    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert first["scheduled_retries"] == 1
    assert second["scheduled_retries"] == 1
    assert first["errors_by_type"] == {"TransientError": 1}
    assert second["errors_by_type"] == {"TransientError": 1}
    assert first["successful_retries"] == 1
    assert second["successful_retries"] == 1


@pytest.mark.asyncio
async def test_repeated_permanent_url_belongs_to_each_run_snapshot() -> None:
    url = "https://example.com/missing"
    crawler = AsyncCrawler(max_concurrent=1, max_attempts=1)
    crawler.request_executor._fetcher = AsyncMock(
        return_value=FetchResult.http_error(url, 404)
    )

    await crawl_urls(crawler, [url])
    first = crawler.get_stats()["request_stats"]
    await crawl_urls(crawler, [url])
    second = crawler.get_stats()["request_stats"]

    assert first["errors_by_type"] == {"PermanentError": 1}
    assert second["errors_by_type"] == {"PermanentError": 1}
    assert first["permanent_error_urls"] == [url]
    assert second["permanent_error_urls"] == [url]


class FakeStorage(DataStorage):
    def __init__(self, outcomes: Iterable[Exception | None] = ()) -> None:
        super().__init__()
        self._outcomes = list(outcomes)

    async def _save(self, data: CrawlRecord) -> None:
        outcome = self._outcomes.pop(0) if self._outcomes else None
        if outcome is not None:
            raise outcome


class OtherFakeStorage(FakeStorage):
    pass


@pytest.mark.asyncio
async def test_storage_failure_does_not_fail_page_and_stats_are_per_run() -> None:
    first = "https://example.com/first"
    second = "https://example.com/second"
    storage = FakeStorage([ValueError("cannot save"), None])
    crawler = AsyncCrawler(max_concurrent=1, storage=storage)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda url: FetchResult.success(
            url,
            HTML,
            content_type="text/html",
        )
    )

    try:
        await crawl_urls(crawler, [first])
        first_stats = crawler.get_stats()
        await crawl_urls(crawler, [second])
        second_stats = crawler.get_stats()

        assert first_stats["successful"] == 1
        assert first_stats["failed"] == 0
        assert first_stats["storage_stats"] == {
            "saved_records": 0,
            "failed_saves": 1,
            "retried_saves": 0,
        }
        assert second_stats["successful"] == 1
        assert second_stats["failed"] == 0
        assert second_stats["storage_stats"] == {
            "saved_records": 1,
            "failed_saves": 0,
            "retried_saves": 0,
        }
    finally:
        await crawler.close()


@pytest.mark.asyncio
async def test_composite_storage_stats_are_detached_and_keyed_per_backend() -> None:
    url = "https://example.com/page"
    composite = CompositeStorage([FakeStorage(), OtherFakeStorage()])
    crawler = AsyncCrawler(max_concurrent=1, storage=composite)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=FetchResult.success(
            url,
            HTML,
            content_type="text/html",
        )
    )

    try:
        await crawl_urls(crawler, [url])
        stats = crawler.get_stats()
        storage_stats = stats["storage_stats"]

        assert storage_stats == {
            "FakeStorage": {
                "saved_records": 1,
                "failed_saves": 0,
                "retried_saves": 0,
            },
            "OtherFakeStorage": {
                "saved_records": 1,
                "failed_saves": 0,
                "retried_saves": 0,
            },
        }
        assert isinstance(storage_stats, dict)
        fake_stats = storage_stats["FakeStorage"]
        assert isinstance(fake_stats, dict)
        fake_stats["saved_records"] = 99
        assert crawler.get_stats()["storage_stats"] != storage_stats
    finally:
        await crawler.close()


@pytest.mark.asyncio
async def test_canonical_snapshot_before_during_and_after_run() -> None:
    first = "https://example.com/first"
    second = "https://example.com/second"
    second_started = asyncio.Event()
    release_second = asyncio.Event()

    async def fetch(url: str) -> FetchResult:
        if url == second:
            second_started.set()
            await release_second.wait()
        return FetchResult.success(url, HTML)

    crawler = AsyncCrawler(max_concurrent=1)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=fetch
    )
    assert crawler.get_stats()["total_pages"] == 0

    crawl_task = asyncio.create_task(crawl_urls(crawler, [first, second]))
    try:
        await asyncio.wait_for(second_started.wait(), timeout=1.0)
        during = crawler.get_stats()
        assert during["pages_scheduled"] == 2
        assert during["pages_queued"] == 0
        assert during["active_tasks"] == 1
        assert during["total_pages"] == 1
    finally:
        release_second.set()
        await crawl_task

    after = crawler.get_stats()
    assert after["pages_scheduled"] == 2
    assert after["pages_queued"] == 0
    assert after["active_tasks"] == 0
    assert after["total_pages"] == 2
