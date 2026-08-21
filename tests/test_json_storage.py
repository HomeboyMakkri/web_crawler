import asyncio
import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.crawl_record import CrawlRecord
from src.json_storage import JSONStorage


class FakeAsyncFile:
    def __init__(
        self,
        filesystem: "FakeAiofiles",
        path: Path,
        mode: str,
    ) -> None:
        self.filesystem = filesystem
        self.path = path
        self.mode = mode
        self.closed = False
        self._lines: Iterator[str] | None = None

    async def write(self, value: str) -> int:
        assert self.mode in {"a", "w"}
        self.filesystem.active_writes += 1
        self.filesystem.max_active_writes = max(
            self.filesystem.max_active_writes,
            self.filesystem.active_writes,
        )
        try:
            # Give concurrently scheduled saves a chance to overlap.
            await asyncio.sleep(0)
            if self.path == self.filesystem.write_error_path:
                raise OSError("simulated write failure")
            self.filesystem.contents[self.path] = (
                self.filesystem.contents.get(self.path, "") + value
            )
            self.filesystem.write_calls.append(value)
            return len(value)
        finally:
            self.filesystem.active_writes -= 1

    async def flush(self) -> None:
        self.filesystem.flush_calls += 1

    async def close(self) -> None:
        self.closed = True
        self.filesystem.closed_files.append(self)

    def __aiter__(self) -> "FakeAsyncFile":
        self._lines = iter(
            self.filesystem.contents.get(self.path, "").splitlines(
                keepends=True
            )
        )
        return self

    async def __anext__(self) -> str:
        assert self._lines is not None
        try:
            return next(self._lines)
        except StopIteration as error:
            raise StopAsyncIteration from error


class FakeAiofiles:
    def __init__(self) -> None:
        self.contents: dict[Path, str] = {}
        self.open_calls: list[tuple[Path, str, str]] = []
        self.write_calls: list[str] = []
        self.flush_calls = 0
        self.closed_files: list[FakeAsyncFile] = []
        self.active_writes = 0
        self.max_active_writes = 0
        self.write_error_path: Path | None = None

    @property
    def content(self) -> str:
        return self.contents.get(Path("records.jsonl"), "")

    @content.setter
    def content(self, value: str) -> None:
        self.contents[Path("records.jsonl")] = value

    async def open(
        self,
        path: str | Path,
        mode: str,
        *,
        encoding: str,
    ) -> FakeAsyncFile:
        resolved_path = Path(path)
        self.open_calls.append((resolved_path, mode, encoding))
        if mode == "r" and resolved_path not in self.contents:
            raise FileNotFoundError(resolved_path)
        if mode == "w":
            self.contents[resolved_path] = ""
        return FakeAsyncFile(self, resolved_path, mode)


@pytest.fixture
def fake_aiofiles(monkeypatch: pytest.MonkeyPatch) -> FakeAiofiles:
    filesystem = FakeAiofiles()
    monkeypatch.setattr("src.json_storage.aiofiles.open", filesystem.open)
    return filesystem


def make_record(
    url: str = "https://example.com",
    *,
    title: str = "Пример",
) -> CrawlRecord:
    return CrawlRecord(
        url=url,
        title=title,
        text="Page text",
        links=["https://example.com/next"],
        metadata={"description": "Тест"},
        crawled_at=datetime(2026, 8, 19, 12, 30, tzinfo=timezone.utc),
        status_code=200,
        content_type="text/html",
    )


async def test_save_appends_one_utf8_json_line(
    fake_aiofiles: FakeAiofiles,
) -> None:
    storage = JSONStorage("records.jsonl")

    await storage.save(make_record())

    lines = fake_aiofiles.content.splitlines()
    assert len(lines) == 1
    assert "Пример" in lines[0]
    assert "\\u041f" not in lines[0]
    assert json.loads(lines[0]) == {
        "url": "https://example.com",
        "title": "Пример",
        "text": "Page text",
        "links": ["https://example.com/next"],
        "metadata": {"description": "Тест"},
        "crawled_at": "2026-08-19T12:30:00+00:00",
        "status_code": 200,
        "content_type": "text/html",
    }
    assert fake_aiofiles.open_calls == [
        (Path("records.jsonl"), "a", "utf-8")
    ]


async def test_save_many_uses_one_batch_write(
    fake_aiofiles: FakeAiofiles,
) -> None:
    storage = JSONStorage("records.jsonl")
    records = [
        make_record("https://example.com/one"),
        make_record("https://example.com/two"),
    ]

    await storage.save_many(records)

    assert len(fake_aiofiles.write_calls) == 1
    assert [
        value["url"]
        for value in map(json.loads, fake_aiofiles.content.splitlines())
    ] == [record.url for record in records]


async def test_empty_batch_does_not_open_file(
    fake_aiofiles: FakeAiofiles,
) -> None:
    storage = JSONStorage("records.jsonl")

    await storage.save_many([])

    assert fake_aiofiles.open_calls == []


async def test_concurrent_saves_cannot_overlap_writes(
    fake_aiofiles: FakeAiofiles,
) -> None:
    storage = JSONStorage("records.jsonl")
    records = [
        make_record(f"https://example.com/{index}")
        for index in range(20)
    ]

    await asyncio.gather(*(storage.save(record) for record in records))

    decoded = [json.loads(line) for line in fake_aiofiles.content.splitlines()]
    assert len(decoded) == len(records)
    assert {value["url"] for value in decoded} == {
        record.url for record in records
    }
    assert fake_aiofiles.max_active_writes == 1
    assert len(fake_aiofiles.open_calls) == 1


async def test_read_records_flushes_and_yields_one_object_per_line(
    fake_aiofiles: FakeAiofiles,
) -> None:
    storage = JSONStorage("records.jsonl")
    records = [make_record(), make_record("https://example.com/two")]
    await storage.save_many(records)

    decoded = [record async for record in storage.read_records()]

    assert [record["url"] for record in decoded] == [
        record.url for record in records
    ]
    assert fake_aiofiles.flush_calls == 1
    assert fake_aiofiles.open_calls[-1] == (
        Path("records.jsonl"),
        "r",
        "utf-8",
    )
    assert fake_aiofiles.closed_files[-1].mode == "r"


async def test_records_can_be_read_after_storage_is_closed(
    fake_aiofiles: FakeAiofiles,
) -> None:
    storage = JSONStorage("records.jsonl")
    await storage.save(make_record())
    await storage.close()
    flushes_after_close = fake_aiofiles.flush_calls

    decoded = [record async for record in storage.read_records()]

    assert len(decoded) == 1
    assert fake_aiofiles.flush_calls == flushes_after_close


async def test_read_records_rejects_non_object_json_line(
    fake_aiofiles: FakeAiofiles,
) -> None:
    fake_aiofiles.content = "[1, 2, 3]\n"
    storage = JSONStorage("records.jsonl")

    with pytest.raises(ValueError, match="line 1 must contain an object"):
        _ = [record async for record in storage.read_records()]

    assert fake_aiofiles.closed_files[-1].mode == "r"


async def test_export_pretty_writes_valid_unicode_json_array(
    fake_aiofiles: FakeAiofiles,
) -> None:
    storage = JSONStorage("records.jsonl")
    record = make_record()
    await storage.save(record)

    await storage.export_pretty("records.json")

    pretty = fake_aiofiles.contents[Path("records.json")]
    assert json.loads(pretty) == [
        {
            **record.to_dict(),
            "crawled_at": record.crawled_at.isoformat(),
        }
    ]
    assert '\n  {\n    "url"' in pretty
    assert "Пример" in pretty
    assert "\\u041f" not in pretty
    assert fake_aiofiles.flush_calls == 1


async def test_export_pretty_streams_multiple_records_with_custom_indent(
    fake_aiofiles: FakeAiofiles,
) -> None:
    storage = JSONStorage("records.jsonl")
    records = [
        make_record("https://example.com/one"),
        make_record("https://example.com/two"),
    ]
    await storage.save_many(records)
    source_before_export = fake_aiofiles.content

    await storage.export_pretty("records.json", indent=4)

    pretty = fake_aiofiles.contents[Path("records.json")]
    assert [record["url"] for record in json.loads(pretty)] == [
        record.url for record in records
    ]
    assert '\n    {\n        "url"' in pretty
    assert fake_aiofiles.content == source_before_export
    assert len(fake_aiofiles.write_calls) > len(records)


async def test_export_pretty_treats_missing_source_as_empty_storage(
    fake_aiofiles: FakeAiofiles,
) -> None:
    storage = JSONStorage("records.jsonl")

    await storage.export_pretty("records.json")

    assert fake_aiofiles.contents[Path("records.json")] == "[]\n"
    assert json.loads(fake_aiofiles.contents[Path("records.json")]) == []


@pytest.mark.parametrize("indent", [0, -1, 1.5, True])
async def test_export_pretty_requires_positive_integer_indent(
    fake_aiofiles: FakeAiofiles,
    indent: object,
) -> None:
    storage = JSONStorage("records.jsonl")

    with pytest.raises(ValueError, match="positive integer"):
        await storage.export_pretty(  # type: ignore[arg-type]
            "records.json",
            indent=indent,
        )

    assert fake_aiofiles.open_calls == []


async def test_export_pretty_rejects_source_as_destination(
    fake_aiofiles: FakeAiofiles,
) -> None:
    storage = JSONStorage("records.jsonl")

    with pytest.raises(ValueError, match="must differ"):
        await storage.export_pretty("./records.jsonl")

    assert fake_aiofiles.open_calls == []


async def test_export_pretty_closes_both_files_after_write_error(
    fake_aiofiles: FakeAiofiles,
) -> None:
    storage = JSONStorage("records.jsonl")
    await storage.save(make_record())
    fake_aiofiles.write_error_path = Path("records.json")

    with pytest.raises(OSError, match="simulated write failure"):
        await storage.export_pretty("records.json")

    assert [file.mode for file in fake_aiofiles.closed_files[-2:]] == [
        "w",
        "r",
    ]


async def test_export_pretty_real_files_match_closed_jsonl(
    tmp_path: Path,
) -> None:
    source = tmp_path / "records.jsonl"
    destination = tmp_path / "records.json"
    storage = JSONStorage(source)
    records = [
        make_record("https://example.com/one"),
        make_record("https://example.com/two"),
    ]
    await storage.save_many(records)
    await storage.close()

    await storage.export_pretty(destination)

    jsonl_records = [
        json.loads(line)
        for line in source.read_text(encoding="utf-8").splitlines()
    ]
    pretty_records = json.loads(destination.read_text(encoding="utf-8"))
    assert pretty_records == jsonl_records
