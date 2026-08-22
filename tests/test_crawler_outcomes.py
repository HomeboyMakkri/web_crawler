import asyncio
from unittest.mock import AsyncMock, call

import pytest

from src.crawl_record import CrawlRecord
from src.crawler import AsyncCrawler
from src.data_storage import DataStorage
from src.errors import ParseError
from src.fetch_result import FetchOutcome, FetchResult


URL = "https://example.com/page"
SECOND_URL = "https://example.com/second"
HTML = "<html><body><p>content</p></body></html>"


async def crawl_one(crawler: AsyncCrawler, url: str = URL) -> None:
    await crawler.crawl(
        [url],
        max_pages=1,
        max_depth=0,
        same_domain_only=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [200, 302])
async def test_successful_page_retains_final_2xx_or_3xx_result(
    status_code: int,
) -> None:
    fetched = FetchResult.success(URL, HTML, status_code=status_code)
    crawler = AsyncCrawler(max_concurrent=1)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=fetched
    )

    await crawl_one(crawler)

    assert crawler.get_page_outcomes() == {URL: fetched}
    assert crawler.get_crawl_stats()["pages_successful"] == 1


@pytest.mark.asyncio
async def test_final_http_error_after_retries_is_retained() -> None:
    crawler = AsyncCrawler(max_concurrent=1)
    fetcher = AsyncMock(return_value=FetchResult.http_error(URL, 503))
    sleep = AsyncMock()
    crawler.request_executor._fetcher = fetcher
    crawler.retry_strategy._sleep = sleep

    await crawl_one(crawler)

    outcome = crawler.get_page_outcomes()[URL]
    assert outcome is not None
    assert outcome.outcome is FetchOutcome.HTTP_ERROR
    assert outcome.status_code == 503
    assert outcome.attempts == 4
    assert fetcher.await_count == 4
    assert sleep.await_args_list == [call(0.5), call(1.0), call(2.0)]
    assert crawler.get_crawl_stats()["pages_failed"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fetched",
    [
        FetchResult.network_error(URL, "connection lost", attempts=4),
        FetchResult.timeout(URL, attempts=4),
    ],
)
async def test_final_failure_without_http_status_is_retained(
    fetched: FetchResult,
) -> None:
    crawler = AsyncCrawler(max_concurrent=1)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=fetched
    )

    await crawl_one(crawler)

    outcome = crawler.get_page_outcomes()[URL]
    assert outcome is not None
    assert outcome is fetched
    assert outcome.status_code is None
    assert crawler.get_crawl_stats()["pages_failed"] == 1


@pytest.mark.asyncio
async def test_parser_failure_retains_successful_fetch_status() -> None:
    fetched = FetchResult.success(URL, HTML, status_code=206)
    crawler = AsyncCrawler(max_concurrent=1)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=fetched
    )
    crawler._parser.parse_html = AsyncMock(  # type: ignore[method-assign]
        side_effect=ParseError("parser crashed", url=URL)
    )

    await crawl_one(crawler)

    assert crawler.get_page_outcomes() == {URL: fetched}
    assert crawler.failed_urls == {URL: "ParseError: parser crashed"}
    assert crawler.get_crawl_stats()["pages_failed"] == 1


@pytest.mark.asyncio
async def test_robots_block_is_retained_and_remains_distinct_from_failure() -> None:
    fetched = FetchResult.blocked(URL)
    crawler = AsyncCrawler(max_concurrent=1)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=fetched
    )

    await crawl_one(crawler)

    assert crawler.get_page_outcomes() == {URL: fetched}
    assert crawler.blocked_urls == {URL: "Blocked by robots.txt"}
    assert crawler.failed_urls == {}
    stats = crawler.get_crawl_stats()
    assert stats["pages_blocked"] == 1
    assert stats["pages_failed"] == 0


@pytest.mark.asyncio
async def test_duplicate_page_is_fetched_and_retained_once() -> None:
    fetched = FetchResult.success(URL, HTML)
    crawler = AsyncCrawler(max_concurrent=1)
    fetch = AsyncMock(return_value=fetched)
    crawler.request_executor.fetch = fetch  # type: ignore[method-assign]

    await crawler.crawl(
        [URL, URL],
        max_pages=2,
        max_depth=0,
        same_domain_only=False,
    )

    fetch.assert_awaited_once_with(URL)
    assert crawler.get_page_outcomes() == {URL: fetched}


@pytest.mark.asyncio
async def test_page_outcomes_reset_between_sequential_runs() -> None:
    crawler = AsyncCrawler(max_concurrent=1)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda url: FetchResult.success(url, HTML)
    )

    await crawl_one(crawler, URL)
    first_run = crawler.get_page_outcomes()
    await crawl_one(crawler, SECOND_URL)

    assert set(first_run) == {URL}
    assert set(crawler.get_page_outcomes()) == {SECOND_URL}


@pytest.mark.asyncio
async def test_page_outcome_snapshot_is_detached() -> None:
    fetched = FetchResult.success(URL, HTML)
    crawler = AsyncCrawler(max_concurrent=1)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=fetched
    )
    await crawl_one(crawler)

    snapshot = crawler.get_page_outcomes()
    snapshot.clear()
    snapshot[SECOND_URL] = None

    assert crawler.get_page_outcomes() == {URL: fetched}


@pytest.mark.asyncio
async def test_outcome_snapshot_excludes_active_page_tasks() -> None:
    second_started = asyncio.Event()
    release_second = asyncio.Event()

    async def fetch(url: str) -> FetchResult:
        if url == SECOND_URL:
            second_started.set()
            await release_second.wait()
        return FetchResult.success(url, HTML)

    crawler = AsyncCrawler(max_concurrent=1)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=fetch
    )
    crawl_task = asyncio.create_task(
        crawler.crawl(
            [URL, SECOND_URL],
            max_pages=2,
            max_depth=0,
            same_domain_only=False,
        )
    )

    try:
        await asyncio.wait_for(second_started.wait(), timeout=1.0)
        assert set(crawler.get_page_outcomes()) == {URL}
    finally:
        release_second.set()
        await crawl_task
    assert set(crawler.get_page_outcomes()) == {URL, SECOND_URL}


class FailingStorage(DataStorage):
    async def _save(self, data: CrawlRecord) -> None:
        raise ValueError("storage failed")


@pytest.mark.asyncio
async def test_storage_failure_does_not_change_successful_page_outcome() -> None:
    storage = FailingStorage()
    fetched = FetchResult.success(
        URL,
        HTML,
        status_code=203,
        content_type="text/html",
    )
    crawler = AsyncCrawler(max_concurrent=1, storage=storage)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=fetched
    )

    try:
        await crawl_one(crawler)

        assert crawler.get_page_outcomes() == {URL: fetched}
        assert set(crawler.processed_urls) == {URL}
        assert crawler.failed_urls == {}
        assert crawler.storage_manager is not None
        assert crawler.storage_manager.get_stats()["failed_saves"] == 1
    finally:
        await crawler.close()


@pytest.mark.asyncio
async def test_terminal_task_without_fetch_result_is_recorded_as_none() -> None:
    crawler = AsyncCrawler(max_concurrent=1)
    crawler.fetch_and_parse = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("failed before fetch")
    )

    await crawl_one(crawler)

    assert crawler.get_page_outcomes() == {URL: None}
    assert crawler.get_crawl_stats()["pages_failed"] == 1
