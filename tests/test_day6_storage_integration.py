from pathlib import Path
from unittest.mock import AsyncMock

from src.composite_storage import CompositeStorage
from src.crawler import AsyncCrawler
from src.csv_storage import CSVStorage
from src.fetch_result import FetchResult
from src.json_storage import JSONStorage
from src.sqlite_storage import SQLiteStorage


URLS = ["https://example.com/one", "https://example.com/two"]


def fetched_page(url: str) -> FetchResult:
    name = url.rsplit("/", 1)[-1]
    return FetchResult.success(
        url,
        (
            f"<html><head><title>{name.title()}</title></head>"
            f"<body><p>Stored page {name}</p></body></html>"
        ),
        content_type="text/html",
    )


async def test_crawler_persists_same_pages_to_all_three_formats(
    tmp_path: Path,
) -> None:
    json_storage = JSONStorage(tmp_path / "pages.jsonl")
    csv_storage = CSVStorage(tmp_path / "pages.csv")
    sqlite_storage = SQLiteStorage(tmp_path / "pages.db", batch_size=10)
    composite = CompositeStorage(
        [json_storage, csv_storage, sqlite_storage]
    )
    crawler = AsyncCrawler(
        max_concurrent=1,
        storage=composite,
    )
    crawler.request_executor.fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda url: fetched_page(url)
    )

    results = await crawler.crawl(
        URLS,
        max_pages=2,
        max_depth=0,
        same_domain_only=False,
    )
    await crawler.close()

    json_records = [
        record async for record in json_storage.read_records()
    ]
    csv_records = await csv_storage.read_records()
    sqlite_reader = SQLiteStorage(tmp_path / "pages.db")
    try:
        sqlite_records = await sqlite_reader.read_records()
    finally:
        await sqlite_reader.close()

    assert set(results) == set(URLS)
    assert crawler.failed_urls == {}
    assert {record["url"] for record in json_records} == set(URLS)
    assert {record.url for record in csv_records} == set(URLS)
    assert {record.url for record in sqlite_records} == set(URLS)
    assert [record["title"] for record in json_records] == ["One", "Two"]
    assert [record.title for record in csv_records] == ["One", "Two"]
    assert [record.title for record in sqlite_records] == ["One", "Two"]
    assert composite.closed is True
    assert all(storage.closed for storage in composite.storages)
    assert composite.get_stats() == {
        "JSONStorage": {
            "saved_records": 2,
            "failed_saves": 0,
            "retried_saves": 0,
        },
        "CSVStorage": {
            "saved_records": 2,
            "failed_saves": 0,
            "retried_saves": 0,
        },
        "SQLiteStorage": {
            "saved_records": 2,
            "failed_saves": 0,
            "retried_saves": 0,
        },
    }
