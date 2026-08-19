import asyncio
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.crawl_record import CrawlRecord
from src.csv_storage import CSVStorage


class FakeAsyncCSVFile:
    def __init__(self, filesystem: "FakeCSVAiofiles") -> None:
        self.filesystem = filesystem
        self.closed = False

    async def tell(self) -> int:
        return len(self.filesystem.content)

    async def write(self, value: str) -> int:
        self.filesystem.active_writes += 1
        self.filesystem.max_active_writes = max(
            self.filesystem.max_active_writes,
            self.filesystem.active_writes,
        )
        try:
            await asyncio.sleep(0)
            self.filesystem.content += value
            self.filesystem.write_calls.append(value)
            return len(value)
        finally:
            self.filesystem.active_writes -= 1

    async def flush(self) -> None:
        self.filesystem.flush_calls += 1

    async def close(self) -> None:
        self.closed = True


class FakeCSVAiofiles:
    def __init__(self) -> None:
        self.content = ""
        self.open_calls: list[tuple[Path, str, str, str]] = []
        self.write_calls: list[str] = []
        self.flush_calls = 0
        self.active_writes = 0
        self.max_active_writes = 0

    async def open(
        self,
        path: str | Path,
        mode: str,
        *,
        encoding: str,
        newline: str,
    ) -> FakeAsyncCSVFile:
        self.open_calls.append((Path(path), mode, encoding, newline))
        return FakeAsyncCSVFile(self)


@pytest.fixture
def fake_aiofiles(monkeypatch: pytest.MonkeyPatch) -> FakeCSVAiofiles:
    filesystem = FakeCSVAiofiles()
    monkeypatch.setattr("src.csv_storage.aiofiles.open", filesystem.open)
    return filesystem


def make_record(
    url: str = "https://example.com",
    *,
    title: str = "Пример",
    text: str = "Page text",
) -> CrawlRecord:
    return CrawlRecord(
        url=url,
        title=title,
        text=text,
        links=["https://example.com/one", "https://example.com/two"],
        metadata={"description": "Тест", "keywords": "async,csv"},
        crawled_at=datetime(2026, 8, 19, 12, 30, tzinfo=timezone.utc),
        status_code=200,
        content_type="text/html",
    )


def read_rows(content: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content, newline="")))


async def test_save_writes_fixed_header_and_utf8_row(
    fake_aiofiles: FakeCSVAiofiles,
) -> None:
    storage = CSVStorage("records.csv")

    await storage.save(make_record())

    reader = csv.DictReader(io.StringIO(fake_aiofiles.content, newline=""))
    rows = list(reader)
    assert tuple(reader.fieldnames or ()) == CSVStorage.FIELDNAMES
    assert len(rows) == 1
    assert rows[0]["title"] == "Пример"
    assert rows[0]["crawled_at"] == "2026-08-19T12:30:00+00:00"
    assert rows[0]["status_code"] == "200"
    assert fake_aiofiles.open_calls == [
        (Path("records.csv"), "a+", "utf-8", "")
    ]


async def test_links_and_metadata_are_json_encoded(
    fake_aiofiles: FakeCSVAiofiles,
) -> None:
    storage = CSVStorage("records.csv")
    record = make_record()

    await storage.save(record)

    row = read_rows(fake_aiofiles.content)[0]
    assert json.loads(row["links"]) == record.links
    assert json.loads(row["metadata"]) == record.metadata
    assert "Тест" in row["metadata"]


async def test_csv_writer_escapes_commas_quotes_and_newlines(
    fake_aiofiles: FakeCSVAiofiles,
) -> None:
    title = 'Title, with "quotes"'
    text = "First line\nSecond, line"
    storage = CSVStorage("records.csv")

    await storage.save(make_record(title=title, text=text))

    rows = read_rows(fake_aiofiles.content)
    assert len(rows) == 1
    assert rows[0]["title"] == title
    assert rows[0]["text"] == text


async def test_save_many_writes_one_header_and_one_batch(
    fake_aiofiles: FakeCSVAiofiles,
) -> None:
    storage = CSVStorage("records.csv")
    records = [
        make_record("https://example.com/one"),
        make_record("https://example.com/two"),
    ]

    await storage.save_many(records)

    assert len(fake_aiofiles.write_calls) == 1
    assert [row["url"] for row in read_rows(fake_aiofiles.content)] == [
        record.url for record in records
    ]
    assert fake_aiofiles.content.count(",".join(CSVStorage.FIELDNAMES)) == 1


async def test_sequential_saves_do_not_repeat_header(
    fake_aiofiles: FakeCSVAiofiles,
) -> None:
    storage = CSVStorage("records.csv")

    await storage.save(make_record("https://example.com/one"))
    await storage.save(make_record("https://example.com/two"))

    assert len(read_rows(fake_aiofiles.content)) == 2
    assert fake_aiofiles.content.count(",".join(CSVStorage.FIELDNAMES)) == 1


async def test_existing_non_empty_file_does_not_receive_another_header(
    fake_aiofiles: FakeCSVAiofiles,
) -> None:
    fake_aiofiles.content = ",".join(CSVStorage.FIELDNAMES) + "\n"
    storage = CSVStorage("records.csv")

    await storage.save(make_record())

    assert fake_aiofiles.content.count(",".join(CSVStorage.FIELDNAMES)) == 1
    assert len(read_rows(fake_aiofiles.content)) == 1


async def test_empty_batch_does_not_open_file(
    fake_aiofiles: FakeCSVAiofiles,
) -> None:
    storage = CSVStorage("records.csv")

    await storage.save_many([])

    assert fake_aiofiles.open_calls == []


async def test_concurrent_saves_are_serialized_by_lock(
    fake_aiofiles: FakeCSVAiofiles,
) -> None:
    storage = CSVStorage("records.csv")
    records = [
        make_record(f"https://example.com/{index}")
        for index in range(20)
    ]

    await asyncio.gather(*(storage.save(record) for record in records))

    rows = read_rows(fake_aiofiles.content)
    assert len(rows) == len(records)
    assert {row["url"] for row in rows} == {record.url for record in records}
    assert fake_aiofiles.max_active_writes == 1
    assert len(fake_aiofiles.open_calls) == 1
    assert fake_aiofiles.content.count(",".join(CSVStorage.FIELDNAMES)) == 1
