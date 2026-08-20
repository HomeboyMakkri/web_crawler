import asyncio
import logging
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from unittest.mock import AsyncMock, call

import pytest

from src.crawl_record import CrawlRecord
from src.data_storage import DataStorage
from src.storage_manager import StorageManager


class FailingStorage(DataStorage):
    def __init__(self, outcomes: Iterable[BaseException | None]) -> None:
        super().__init__()
        self._outcomes = list(outcomes)
        self.attempts = 0
        self.records: list[CrawlRecord] = []
        self.events: list[str] = []

    async def _save(self, data: CrawlRecord) -> None:
        self.attempts += 1
        outcome = self._outcomes.pop(0) if self._outcomes else None
        if outcome is not None:
            raise outcome
        self.records.append(data)

    async def _flush(self) -> None:
        self.events.append("flush")

    async def _close(self) -> None:
        self.events.append("close")


def make_record(url: str = "https://example.com") -> CrawlRecord:
    return CrawlRecord(
        url=url,
        title="Example",
        text="Page text",
        links=[],
        metadata={},
        crawled_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        status_code=200,
        content_type="text/html",
    )


def empty_stats() -> dict[str, int]:
    return {
        "saved_records": 0,
        "failed_saves": 0,
        "retried_saves": 0,
    }


async def test_successful_save_returns_true_and_counts_record() -> None:
    storage = FailingStorage([None])
    sleep = AsyncMock()
    manager = StorageManager(storage, sleep=sleep)
    record = make_record()

    result = await manager.save(record)

    assert result is True
    assert storage.records == [record]
    sleep.assert_not_awaited()
    assert manager.get_stats() == {
        **empty_stats(),
        "saved_records": 1,
    }


async def test_temporary_errors_are_retried_until_success() -> None:
    storage = FailingStorage(
        [OSError("busy"), OSError("still busy"), None]
    )
    sleep = AsyncMock()
    manager = StorageManager(
        storage,
        max_retries=3,
        base_delay=0.25,
        backoff_factor=2.0,
        sleep=sleep,
    )

    result = await manager.save(make_record())

    assert result is True
    assert storage.attempts == 3
    assert sleep.await_args_list == [call(0.25), call(0.5)]
    assert manager.get_stats() == {
        "saved_records": 1,
        "failed_saves": 0,
        "retried_saves": 2,
    }


async def test_sqlite_operational_error_is_temporary_by_default() -> None:
    storage = FailingStorage(
        [sqlite3.OperationalError("database is locked"), None]
    )
    sleep = AsyncMock()
    manager = StorageManager(storage, max_retries=1, sleep=sleep)

    assert await manager.save(make_record()) is True

    assert storage.attempts == 2
    assert manager.get_stats()["retried_saves"] == 1


async def test_exhausted_save_returns_false_instead_of_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="src.storage_manager")
    storage = FailingStorage([OSError("disk busy") for _ in range(3)])
    sleep = AsyncMock()
    manager = StorageManager(storage, max_retries=2, sleep=sleep)

    result = await manager.save(make_record())

    assert result is False
    assert storage.attempts == 3
    assert manager.get_stats() == {
        "saved_records": 0,
        "failed_saves": 1,
        "retried_saves": 2,
    }
    assert "Storage save failed" in caplog.text
    assert "url=https://example.com" in caplog.text


async def test_non_temporary_error_is_not_retried() -> None:
    storage = FailingStorage([ValueError("invalid row")])
    sleep = AsyncMock()
    manager = StorageManager(storage, sleep=sleep)

    result = await manager.save(make_record())

    assert result is False
    assert storage.attempts == 1
    sleep.assert_not_awaited()
    assert manager.get_stats() == {
        "saved_records": 0,
        "failed_saves": 1,
        "retried_saves": 0,
    }


async def test_crawler_can_continue_saving_after_one_failed_record() -> None:
    storage = FailingStorage([ValueError("broken row"), None])
    manager = StorageManager(storage, sleep=AsyncMock())
    failed_record = make_record("https://example.com/broken")
    next_record = make_record("https://example.com/next")

    first_result = await manager.save(failed_record)
    second_result = await manager.save(next_record)

    assert first_result is False
    assert second_result is True
    assert storage.records == [next_record]
    assert manager.get_stats() == {
        "saved_records": 1,
        "failed_saves": 1,
        "retried_saves": 0,
    }


async def test_custom_retry_error_types_are_supported() -> None:
    class TemporaryStorageError(Exception):
        pass

    storage = FailingStorage([TemporaryStorageError("later"), None])
    sleep = AsyncMock()
    manager = StorageManager(
        storage,
        max_retries=1,
        retry_on=[TemporaryStorageError],
        sleep=sleep,
    )

    assert await manager.save(make_record()) is True
    assert storage.attempts == 2
    assert manager.get_stats()["retried_saves"] == 1


async def test_cancellation_is_not_converted_into_failed_save() -> None:
    storage = FailingStorage([asyncio.CancelledError()])
    manager = StorageManager(storage, sleep=AsyncMock())

    with pytest.raises(asyncio.CancelledError):
        await manager.save(make_record())

    assert manager.get_stats() == empty_stats()


def test_storage_must_implement_data_storage() -> None:
    with pytest.raises(ValueError, match="DataStorage"):
        StorageManager(object())  # type: ignore[arg-type]


async def test_close_flushes_and_closes_underlying_storage() -> None:
    storage = FailingStorage([])
    manager = StorageManager(storage, sleep=AsyncMock())

    result = await manager.close()

    assert result is True
    assert storage.closed is True
    assert storage.events == ["flush", "close"]


async def test_close_error_is_reported_without_escaping() -> None:
    class FailingCloseStorage(FailingStorage):
        async def _flush(self) -> None:
            raise OSError("flush failed")

    storage = FailingCloseStorage([])
    manager = StorageManager(storage, sleep=AsyncMock())

    result = await manager.close()

    assert result is False
    assert storage.closed is False
    assert manager.get_stats() == empty_stats()
