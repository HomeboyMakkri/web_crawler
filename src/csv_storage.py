"""Asynchronous CSV storage for crawl records."""

import asyncio
import csv
import io
import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import aiofiles

from .crawl_record import CrawlRecord
from .data_storage import DataStorage


class CSVStorage(DataStorage):
    """Append crawl records using one stable CSV schema."""

    FIELDNAMES: Final[tuple[str, ...]] = (
        "url",
        "title",
        "text",
        "links",
        "metadata",
        "crawled_at",
        "status_code",
        "content_type",
    )

    def __init__(
        self,
        path: str | Path,
        *,
        encoding: str = "utf-8",
    ) -> None:
        super().__init__()
        self.path = Path(path)
        self.encoding = encoding
        self._write_lock = asyncio.Lock()
        self._file: Any | None = None
        self._header_written = False

    async def _save(self, data: CrawlRecord) -> None:
        row = self._serialize_rows((data,))
        async with self._write_lock:
            await self._write_rows(row)

    async def _save_many(self, data: Iterable[CrawlRecord]) -> None:
        rows = self._serialize_rows(data)
        if not rows:
            return

        async with self._write_lock:
            await self._write_rows(rows)

    async def _flush(self) -> None:
        async with self._write_lock:
            if self._file is not None:
                await self._file.flush()

    async def _close(self) -> None:
        async with self._write_lock:
            if self._file is not None:
                await self._file.close()
                self._file = None

    async def read_records(self) -> list[CrawlRecord]:
        """Read this CSV back into records for verification and demos.

        CSV fields may contain embedded newlines, so the standard CSV parser
        must see the complete character stream rather than physical lines.
        """
        if not self.closed:
            await self.flush()

        file = await aiofiles.open(
            self.path,
            "r",
            encoding=self.encoding,
            newline="",
        )
        try:
            content = await file.read()
        finally:
            await file.close()

        reader = csv.DictReader(io.StringIO(content, newline=""))
        if reader.fieldnames is None:
            return []
        if tuple(reader.fieldnames) != self.FIELDNAMES:
            raise ValueError("CSV header does not match CrawlRecord schema")
        return [self._from_row(row) for row in reader]

    async def _write_rows(self, rows: str) -> None:
        file = await self._get_writer()
        payload = rows
        if not self._header_written:
            payload = self._serialize_header() + rows

        await file.write(payload)
        self._header_written = True

    async def _get_writer(self) -> Any:
        if self._file is None:
            self._file = await aiofiles.open(
                self.path,
                "a+",
                encoding=self.encoding,
                newline="",
            )
            self._header_written = await self._file.tell() > 0
        return self._file

    @classmethod
    def _serialize_header(cls) -> str:
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(
            buffer,
            fieldnames=cls.FIELDNAMES,
            lineterminator="\n",
        )
        writer.writeheader()
        return buffer.getvalue()

    @classmethod
    def _serialize_rows(cls, records: Iterable[CrawlRecord]) -> str:
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(
            buffer,
            fieldnames=cls.FIELDNAMES,
            lineterminator="\n",
        )
        for record in records:
            writer.writerow(cls._to_row(record))
        return buffer.getvalue()

    @staticmethod
    def _to_row(record: CrawlRecord) -> dict[str, object]:
        return {
            "url": record.url,
            "title": record.title,
            "text": record.text,
            "links": json.dumps(record.links, ensure_ascii=False),
            "metadata": json.dumps(record.metadata, ensure_ascii=False),
            "crawled_at": record.crawled_at.isoformat(),
            "status_code": record.status_code,
            "content_type": record.content_type,
        }

    @staticmethod
    def _from_row(row: dict[str, str | None]) -> CrawlRecord:
        try:
            return CrawlRecord(
                url=row["url"] or "",
                title=row["title"] or "",
                text=row["text"] or "",
                links=json.loads(row["links"] or "null"),
                metadata=json.loads(row["metadata"] or "null"),
                crawled_at=datetime.fromisoformat(row["crawled_at"] or ""),
                status_code=int(row["status_code"] or ""),
                content_type=row["content_type"] or "",
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("invalid CrawlRecord CSV row") from error
