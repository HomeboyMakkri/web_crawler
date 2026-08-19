from collections.abc import Iterable
from datetime import datetime, timezone

import pytest

from src.crawl_record import CrawlRecord
from src.data_storage import DataStorage


def make_record(url: str = "https://example.com") -> CrawlRecord:
    return CrawlRecord(
        url=url,
        title="Example",
        text="Page text",
        links=[],
        metadata={},
        crawled_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        status_code=200,
        content_type="text/html",
    )


class MemoryStorage(DataStorage):
    """Test double: verifies the contract without touching files or a DB."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[CrawlRecord] = []
        self.events: list[str] = []

    async def _save(self, data: CrawlRecord) -> None:
        self.events.append(f"save:{data.url}")
        self.records.append(data)

    async def _flush(self) -> None:
        self.events.append("flush")

    async def _close(self) -> None:
        self.events.append("close")


class BatchMemoryStorage(MemoryStorage):
    def __init__(self) -> None:
        super().__init__()
        self.batch_calls = 0

    async def _save_many(self, data: Iterable[CrawlRecord]) -> None:
        self.batch_calls += 1
        self.records.extend(data)


def test_data_storage_is_abstract() -> None:
    with pytest.raises(TypeError):
        DataStorage()


async def test_save_delegates_one_record_to_backend() -> None:
    storage = MemoryStorage()
    record = make_record()

    await storage.save(record)

    assert storage.records == [record]


async def test_default_save_many_preserves_iteration_order() -> None:
    storage = MemoryStorage()
    records = [
        make_record("https://example.com/one"),
        make_record("https://example.com/two"),
    ]

    await storage.save_many(record for record in records)

    assert storage.records == records
    assert storage.events == [
        "save:https://example.com/one",
        "save:https://example.com/two",
    ]


async def test_backend_can_supply_native_batch_implementation() -> None:
    storage = BatchMemoryStorage()
    records = [make_record(), make_record("https://example.com/two")]

    await storage.save_many(records)

    assert storage.batch_calls == 1
    assert storage.records == records
    assert storage.events == []


async def test_flush_delegates_to_backend() -> None:
    storage = MemoryStorage()

    await storage.flush()

    assert storage.events == ["flush"]


async def test_close_flushes_before_releasing_resources() -> None:
    storage = MemoryStorage()

    await storage.close()

    assert storage.closed is True
    assert storage.events == ["flush", "close"]


async def test_close_is_idempotent() -> None:
    storage = MemoryStorage()

    await storage.close()
    await storage.close()

    assert storage.events == ["flush", "close"]


@pytest.mark.parametrize("operation", ["save", "save_many", "flush"])
async def test_operations_are_rejected_after_close(operation: str) -> None:
    storage = MemoryStorage()
    await storage.close()

    with pytest.raises(RuntimeError, match="storage is closed"):
        if operation == "save":
            await storage.save(make_record())
        elif operation == "save_many":
            await storage.save_many([make_record()])
        else:
            await storage.flush()


async def test_failed_close_does_not_mark_storage_as_closed() -> None:
    class FailingFlushStorage(MemoryStorage):
        async def _flush(self) -> None:
            raise OSError("flush failed")

    storage = FailingFlushStorage()

    with pytest.raises(OSError, match="flush failed"):
        await storage.close()

    assert storage.closed is False
    assert storage.events == []
