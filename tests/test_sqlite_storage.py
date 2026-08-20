import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, AsyncIterator

import aiosqlite
import pytest

from src.crawl_record import CrawlRecord
from src.sqlite_storage import SQLiteStorage


def make_record(
    url: str = "https://example.com",
    *,
    title: str = "Пример",
    status_code: int = 200,
) -> CrawlRecord:
    return CrawlRecord(
        url=url,
        title=title,
        text="Page text",
        links=["https://example.com/next"],
        metadata={"description": "Тест"},
        crawled_at=datetime(2026, 8, 19, 12, 30, tzinfo=timezone.utc),
        status_code=status_code,
        content_type="text/html",
    )


@asynccontextmanager
async def opened_storage(
    path: Path,
    *,
    batch_size: int = 100,
) -> AsyncGenerator[SQLiteStorage, None]:
    storage = SQLiteStorage(path, batch_size=batch_size)
    try:
        yield storage
    finally:
        await storage.close()


async def fetch_value(path: Path, query: str) -> object:
    async with aiosqlite.connect(path) as connection:
        cursor = await connection.execute(query)
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
    assert row is not None
    return row[0]


@pytest.mark.parametrize("batch_size", [0, -1, 1.5, True])
def test_batch_size_must_be_a_positive_integer(
    tmp_path: Path,
    batch_size: object,
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        SQLiteStorage(tmp_path / "crawler.db", batch_size=batch_size)  # type: ignore[arg-type]


async def test_init_db_creates_table_and_indexes(tmp_path: Path) -> None:
    path = tmp_path / "crawler.db"
    async with opened_storage(path) as storage:
        await storage.init_db()

        async with aiosqlite.connect(path) as connection:
            table_cursor = await connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'crawl_records'"
            )
            table = await table_cursor.fetchone()
            await table_cursor.close()
            index_cursor = await connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND tbl_name = 'crawl_records'"
            )
            indexes = {row[0] for row in await index_cursor.fetchall()}
            await index_cursor.close()

        assert table == ("crawl_records",)
        assert "idx_crawl_records_crawled_at" in indexes
        assert "idx_crawl_records_status_code" in indexes


async def test_save_buffers_until_explicit_flush(tmp_path: Path) -> None:
    path = tmp_path / "crawler.db"
    async with opened_storage(path, batch_size=10) as storage:
        await storage.init_db()
        await storage.save(make_record())

        assert storage.pending_count == 1
        assert await fetch_value(path, "SELECT COUNT(*) FROM crawl_records") == 0

        await storage.flush()

        assert storage.pending_count == 0
        assert await fetch_value(path, "SELECT COUNT(*) FROM crawl_records") == 1


async def test_reaching_batch_size_commits_automatically(tmp_path: Path) -> None:
    path = tmp_path / "crawler.db"
    async with opened_storage(path, batch_size=2) as storage:
        await storage.save(make_record("https://example.com/one"))
        assert storage.pending_count == 1

        await storage.save(make_record("https://example.com/two"))

        assert storage.pending_count == 0
        assert await fetch_value(path, "SELECT COUNT(*) FROM crawl_records") == 2


async def test_save_many_flushes_full_batches_and_keeps_remainder(
    tmp_path: Path,
) -> None:
    path = tmp_path / "crawler.db"
    records = [
        make_record(f"https://example.com/{index}")
        for index in range(3)
    ]
    async with opened_storage(path, batch_size=2) as storage:
        await storage.save_many(records)

        assert storage.pending_count == 1
        assert await fetch_value(path, "SELECT COUNT(*) FROM crawl_records") == 2

        await storage.flush()

        assert await storage.read_records() == records


async def test_read_records_restores_complete_crawl_records(
    tmp_path: Path,
) -> None:
    path = tmp_path / "crawler.db"
    record = make_record(title="Страница")
    async with opened_storage(path) as storage:
        await storage.save(record)

        restored = await storage.read_records()

        assert restored == [record]
        assert restored[0].links == ["https://example.com/next"]
        assert restored[0].metadata == {"description": "Тест"}
        assert restored[0].crawled_at.tzinfo is timezone.utc


async def test_saving_same_url_updates_existing_row(tmp_path: Path) -> None:
    path = tmp_path / "crawler.db"
    async with opened_storage(path, batch_size=1) as storage:
        await storage.save(make_record(title="Old title"))
        await storage.save(make_record(title="New title", status_code=203))

        restored = await storage.read_records()

        assert len(restored) == 1
        assert restored[0].title == "New title"
        assert restored[0].status_code == 203


async def test_concurrent_saves_are_not_lost(tmp_path: Path) -> None:
    path = tmp_path / "crawler.db"
    records = [
        make_record(f"https://example.com/{index}")
        for index in range(25)
    ]
    async with opened_storage(path, batch_size=4) as storage:
        await asyncio.gather(*(storage.save(record) for record in records))

        restored = await storage.read_records()

        assert len(restored) == len(records)
        assert {record.url for record in restored} == {
            record.url for record in records
        }


async def test_close_flushes_remaining_buffer(tmp_path: Path) -> None:
    path = tmp_path / "crawler.db"
    storage = SQLiteStorage(path, batch_size=10)
    await storage.save(make_record())

    await storage.close()

    assert storage.closed is True
    assert storage.pending_count == 0
    assert await fetch_value(path, "SELECT COUNT(*) FROM crawl_records") == 1
    await storage.close()
