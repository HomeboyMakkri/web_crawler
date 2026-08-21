from collections.abc import Iterable
from datetime import datetime, timezone

import pytest

from src.composite_storage import CompositeStorage, CompositeStorageError
from src.crawl_record import CrawlRecord
from src.data_storage import DataStorage


class FakeStorage(DataStorage):
    def __init__(
        self,
        outcomes: Iterable[Exception | None] = (),
        *,
        fail_flush: bool = False,
    ) -> None:
        super().__init__()
        self._outcomes = list(outcomes)
        self._fail_flush = fail_flush
        self.records: list[CrawlRecord] = []
        self.attempts = 0
        self.events: list[str] = []

    async def _save(self, data: CrawlRecord) -> None:
        self.attempts += 1
        outcome = self._outcomes.pop(0) if self._outcomes else None
        if outcome is not None:
            raise outcome
        self.records.append(data)

    async def _flush(self) -> None:
        self.events.append("flush")
        if self._fail_flush:
            raise OSError("flush failed")

    async def _close(self) -> None:
        self.events.append("close")


class OtherFakeStorage(FakeStorage):
    pass


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


@pytest.mark.parametrize("storages", [[], [object()]])
def test_composite_requires_data_storages(storages: list[object]) -> None:
    with pytest.raises(ValueError, match="storages"):
        CompositeStorage(storages)  # type: ignore[arg-type]


def test_composite_rejects_same_instance_twice() -> None:
    storage = FakeStorage()

    with pytest.raises(ValueError, match="same storage"):
        CompositeStorage([storage, storage])


async def test_one_record_is_fanned_out_to_every_storage() -> None:
    first = FakeStorage()
    second = OtherFakeStorage()
    composite = CompositeStorage([first, second])
    record = make_record()

    await composite.save(record)

    assert first.records == [record]
    assert second.records == [record]
    assert composite.storages == (first, second)
    assert composite.get_stats() == {
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


async def test_child_retry_does_not_duplicate_other_storage() -> None:
    stable = FakeStorage()
    recovering = OtherFakeStorage([OSError("busy"), None])
    composite = CompositeStorage([stable, recovering])
    record = make_record()

    await composite.save(record)

    assert stable.records == [record]
    assert stable.attempts == 1
    assert recovering.records == [record]
    assert recovering.attempts == 2
    assert composite.get_stats()["OtherFakeStorage"] == {
        "saved_records": 1,
        "failed_saves": 0,
        "retried_saves": 1,
    }


async def test_permanent_child_failure_is_reported_after_other_saves() -> None:
    successful = FakeStorage()
    failing = OtherFakeStorage([ValueError("invalid row")])
    composite = CompositeStorage([successful, failing])

    with pytest.raises(CompositeStorageError) as captured:
        await composite.save(make_record())

    assert captured.value.operation == "save"
    assert captured.value.failed_storages == ("OtherFakeStorage",)
    assert successful.records == [make_record()]
    assert failing.records == []
    assert composite.get_stats()["OtherFakeStorage"]["failed_saves"] == 1


async def test_save_many_fans_out_each_record() -> None:
    first = FakeStorage()
    second = OtherFakeStorage()
    composite = CompositeStorage([first, second])
    records = [make_record(), make_record("https://example.com/two")]

    await composite.save_many(records)

    assert first.records == records
    assert second.records == records


async def test_flush_attempts_every_child_and_reports_failures() -> None:
    failing = FakeStorage(fail_flush=True)
    successful = OtherFakeStorage()
    composite = CompositeStorage([failing, successful])

    with pytest.raises(CompositeStorageError) as captured:
        await composite.flush()

    assert captured.value.failed_storages == ("FakeStorage",)
    assert failing.events == ["flush"]
    assert successful.events == ["flush"]


async def test_close_closes_every_child_and_is_idempotent() -> None:
    first = FakeStorage()
    second = OtherFakeStorage()
    composite = CompositeStorage([first, second])

    await composite.close()
    await composite.close()

    assert composite.closed is True
    assert first.closed is True
    assert second.closed is True
    assert first.events == ["flush", "close"]
    assert second.events == ["flush", "close"]
