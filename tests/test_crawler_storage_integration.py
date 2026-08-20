from collections.abc import Iterable
from unittest.mock import AsyncMock

import pytest

from src.crawl_record import CrawlRecord
from src.crawler import AsyncCrawler
from src.data_storage import DataStorage
from src.fetch_result import FetchResult


FIRST_URL = "https://example.com/one"
SECOND_URL = "https://example.com/two"


class FakeStorage(DataStorage):
    def __init__(self, outcomes: Iterable[Exception | None] = ()) -> None:
        super().__init__()
        self._outcomes = list(outcomes)
        self.records: list[CrawlRecord] = []
        self.events: list[str] = []

    async def _save(self, data: CrawlRecord) -> None:
        outcome = self._outcomes.pop(0) if self._outcomes else None
        if outcome is not None:
            raise outcome
        self.records.append(data)

    async def _flush(self) -> None:
        self.events.append("flush")

    async def _close(self) -> None:
        self.events.append("close")


def successful_page(url: str) -> FetchResult:
    html = f"""
        <html>
          <head>
            <title>Stored {url.rsplit('/', 1)[-1]}</title>
            <meta name="description" content="Storage integration">
          </head>
          <body><p>Page text</p><a href="/next">Next</a></body>
        </html>
    """
    return FetchResult.success(
        url,
        html,
        status_code=203,
        content_type="text/html",
    )


async def test_fetch_and_parse_saves_complete_crawl_record() -> None:
    storage = FakeStorage()
    crawler = AsyncCrawler(storage=storage)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=successful_page(FIRST_URL)
    )

    result = await crawler.fetch_and_parse(FIRST_URL)

    assert result["title"] == "Stored one"
    assert len(storage.records) == 1
    record = storage.records[0]
    assert record.url == FIRST_URL
    assert record.title == "Stored one"
    assert record.text == "Page text Next"
    assert record.links == ["https://example.com/next"]
    assert record.metadata["description"] == "Storage integration"
    assert record.status_code == 203
    assert record.content_type == "text/html"
    assert record.crawled_at.utcoffset() is not None

    await crawler.close()


async def test_storage_failure_does_not_fail_page_or_stop_worker() -> None:
    storage = FakeStorage([ValueError("cannot serialize"), None])
    crawler = AsyncCrawler(max_concurrent=1, storage=storage)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda url: successful_page(url)
    )

    results = await crawler.crawl(
        [FIRST_URL, SECOND_URL],
        max_pages=2,
        max_depth=0,
        same_domain_only=False,
    )

    assert set(results) == {FIRST_URL, SECOND_URL}
    assert crawler.failed_urls == {}
    assert crawler.get_crawl_stats()["pages_successful"] == 2
    assert crawler.get_crawl_stats()["pages_failed"] == 0
    assert [record.url for record in storage.records] == [SECOND_URL]
    assert crawler.storage_manager is not None
    assert crawler.storage_manager.get_stats() == {
        "saved_records": 1,
        "failed_saves": 1,
        "retried_saves": 0,
    }

    await crawler.close()


async def test_unsuccessful_fetch_is_not_sent_to_storage() -> None:
    storage = FakeStorage()
    crawler = AsyncCrawler(storage=storage)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=FetchResult.http_error(FIRST_URL, 404)
    )

    result = await crawler.fetch_and_parse(FIRST_URL)

    assert result["error"] == "Error: HTTP 404"
    assert storage.records == []
    assert crawler.storage_manager is not None
    assert crawler.storage_manager.get_stats()["saved_records"] == 0

    await crawler.close()


async def test_parser_result_with_error_is_not_sent_to_storage() -> None:
    storage = FakeStorage()
    crawler = AsyncCrawler(storage=storage)
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=successful_page(FIRST_URL)
    )
    crawler._parser.parse_html = AsyncMock(  # type: ignore[method-assign]
        return_value=crawler._parser.empty_result(
            FIRST_URL,
            error="broken HTML",
        )
    )

    result = await crawler.fetch_and_parse(FIRST_URL)

    assert result["error"] == "broken HTML"
    assert storage.records == []

    await crawler.close()


async def test_crawler_close_flushes_and_closes_storage() -> None:
    storage = FakeStorage()
    crawler = AsyncCrawler(storage=storage)

    await crawler.close()
    await crawler.close()

    assert storage.closed is True
    assert storage.events == ["flush", "close"]


async def test_storage_close_error_does_not_prevent_http_cleanup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingFlushStorage(FakeStorage):
        async def _flush(self) -> None:
            raise OSError("flush failed")

    storage = FailingFlushStorage()
    crawler = AsyncCrawler(storage=storage)
    crawler.politeness_manager.close = AsyncMock()  # type: ignore[method-assign]
    crawler._transport.close = AsyncMock()  # type: ignore[method-assign]

    await crawler.close()

    crawler.politeness_manager.close.assert_awaited_once()
    crawler._transport.close.assert_awaited_once()
    assert storage.closed is False
    assert "Storage close failed" in caplog.text
