"""Asynchronous JSON Lines storage for crawl records."""

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles

from .crawl_record import CrawlRecord
from .data_storage import DataStorage


class JSONStorage(DataStorage):
    """Append crawl records as independent UTF-8 JSON lines."""

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

    async def _save(self, data: CrawlRecord) -> None:
        line = self._serialize(data)
        async with self._write_lock:
            file = await self._get_writer()
            await file.write(line)

    async def _save_many(self, data: Iterable[CrawlRecord]) -> None:
        lines = [self._serialize(record) for record in data]
        if not lines:
            return

        async with self._write_lock:
            file = await self._get_writer()
            await file.write("".join(lines))

    async def _flush(self) -> None:
        async with self._write_lock:
            if self._file is not None:
                await self._file.flush()

    async def _close(self) -> None:
        async with self._write_lock:
            if self._file is not None:
                await self._file.close()
                self._file = None

    async def read_records(self) -> AsyncIterator[dict[str, object]]:
        """Yield decoded objects one line at a time.

        Reading uses a separate descriptor. Pending writes are flushed first
        while the storage is open; a closed storage can still be inspected.
        """
        if not self.closed:
            await self.flush()

        file = await aiofiles.open(self.path, "r", encoding=self.encoding)
        try:
            async for line_number, line in self._enumerate_lines(file):
                if not line.strip():
                    continue
                yield self._decode_line(line, line_number)
        finally:
            await file.close()

    async def export_pretty(
        self,
        destination: str | Path,
        *,
        indent: int = 2,
    ) -> None:
        """Export the JSONL records as one human-readable JSON array.

        The source remains JSON Lines so normal saves stay append-only and
        scalable. Exporting holds the write lock to produce a consistent
        snapshot without loading all records into memory.
        """
        if isinstance(indent, bool) or not isinstance(indent, int) or indent <= 0:
            raise ValueError("indent must be a positive integer")

        destination_path = Path(destination)
        if self.path.resolve() == destination_path.resolve():
            raise ValueError("destination must differ from the JSONL source")

        async with self._write_lock:
            if not self.closed and self._file is not None:
                await self._file.flush()
            await self._export_pretty_locked(destination_path, indent)

    async def _get_writer(self) -> Any:
        if self._file is None:
            self._file = await aiofiles.open(
                self.path,
                "a",
                encoding=self.encoding,
            )
        return self._file

    async def _export_pretty_locked(
        self,
        destination: Path,
        indent: int,
    ) -> None:
        reader: Any | None = None
        writer: Any | None = None
        try:
            try:
                reader = await aiofiles.open(
                    self.path,
                    "r",
                    encoding=self.encoding,
                )
            except FileNotFoundError:
                # A storage that has never accepted data is an empty dataset.
                pass

            writer = await aiofiles.open(
                destination,
                "w",
                encoding=self.encoding,
            )
            await writer.write("[")

            first_record = True
            if reader is not None:
                async for line_number, line in self._enumerate_lines(reader):
                    if not line.strip():
                        continue
                    record = self._decode_line(line, line_number)
                    formatted = json.dumps(
                        record,
                        ensure_ascii=False,
                        indent=indent,
                    )
                    prefix = " " * indent
                    indented = prefix + formatted.replace(
                        "\n",
                        "\n" + prefix,
                    )
                    await writer.write(
                        ("\n" if first_record else ",\n") + indented
                    )
                    first_record = False

            await writer.write("\n]\n" if not first_record else "]\n")
        finally:
            try:
                if writer is not None:
                    await writer.close()
            finally:
                if reader is not None:
                    await reader.close()

    @staticmethod
    def _serialize(record: CrawlRecord) -> str:
        return (
            json.dumps(
                record.to_dict(),
                ensure_ascii=False,
                default=JSONStorage._json_default,
            )
            + "\n"
        )

    @staticmethod
    def _json_default(value: object) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(
            f"Object of type {type(value).__name__} is not JSON serializable"
        )

    @staticmethod
    def _decode_line(line: str, line_number: int) -> dict[str, object]:
        decoded = json.loads(line)
        if not isinstance(decoded, dict):
            raise ValueError(
                f"JSONL line {line_number} must contain an object"
            )
        return decoded

    @staticmethod
    async def _enumerate_lines(file: Any) -> AsyncIterator[tuple[int, str]]:
        line_number = 0
        async for line in file:
            line_number += 1
            yield line_number, line
