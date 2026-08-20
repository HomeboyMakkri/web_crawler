"""Buffered asynchronous SQLite storage for crawl records."""

import asyncio
import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import aiosqlite

from .crawl_record import CrawlRecord
from .data_storage import DataStorage


class SQLiteStorage(DataStorage):
    """Persist crawl records using buffered SQLite transactions."""

    _CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS crawl_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            text TEXT NOT NULL,
            links TEXT NOT NULL,
            metadata TEXT NOT NULL,
            crawled_at TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            content_type TEXT NOT NULL
        )
    """
    _CREATE_INDEXES = (
        """
        CREATE INDEX IF NOT EXISTS idx_crawl_records_crawled_at
        ON crawl_records(crawled_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_crawl_records_status_code
        ON crawl_records(status_code)
        """,
    )
    _UPSERT = """
        INSERT INTO crawl_records (
            url,
            title,
            text,
            links,
            metadata,
            crawled_at,
            status_code,
            content_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            title = excluded.title,
            text = excluded.text,
            links = excluded.links,
            metadata = excluded.metadata,
            crawled_at = excluded.crawled_at,
            status_code = excluded.status_code,
            content_type = excluded.content_type
    """
    _SELECT = """
        SELECT
            url,
            title,
            text,
            links,
            metadata,
            crawled_at,
            status_code,
            content_type
        FROM crawl_records
        ORDER BY id
    """

    def __init__(
        self,
        path: str | Path,
        *,
        batch_size: int = 100,
    ) -> None:
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or batch_size <= 0
        ):
            raise ValueError("batch_size must be a positive integer")

        super().__init__()
        self.path = Path(path)
        self.batch_size = batch_size
        self._buffer: list[CrawlRecord] = []
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def pending_count(self) -> int:
        """Number of records accepted but not committed yet."""
        return len(self._buffer)

    async def init_db(self) -> None:
        """Open the database and create the table and indexes."""
        self._ensure_open()
        async with self._lock:
            await self._ensure_connection_locked()

    async def _save(self, data: CrawlRecord) -> None:
        async with self._lock:
            self._buffer.append(data)
            if len(self._buffer) >= self.batch_size:
                await self._flush_locked()

    async def _save_many(self, data: Iterable[CrawlRecord]) -> None:
        async with self._lock:
            for record in data:
                self._buffer.append(record)
                if len(self._buffer) >= self.batch_size:
                    await self._flush_locked()

    async def _flush(self) -> None:
        async with self._lock:
            await self._flush_locked()

    async def _close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                await self._connection.close()
                self._connection = None

    async def read_records(self) -> list[CrawlRecord]:
        """Flush and return all stored records in insertion order."""
        self._ensure_open()
        async with self._lock:
            await self._flush_locked()
            connection = await self._ensure_connection_locked()
            cursor = await connection.execute(self._SELECT)
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()

        return [self._row_to_record(row) for row in rows]

    async def _flush_locked(self) -> None:
        if not self._buffer:
            return

        connection = await self._ensure_connection_locked()
        parameters = [self._to_row(record) for record in self._buffer]
        try:
            await connection.executemany(self._UPSERT, parameters)
            await connection.commit()
        except BaseException:
            await connection.rollback()
            raise
        else:
            self._buffer.clear()

    async def _ensure_connection_locked(self) -> aiosqlite.Connection:
        if self._connection is not None:
            return self._connection

        connection = await aiosqlite.connect(self.path)
        try:
            await connection.execute(self._CREATE_TABLE)
            for statement in self._CREATE_INDEXES:
                await connection.execute(statement)
            await connection.commit()
        except BaseException:
            await connection.close()
            raise

        self._connection = connection
        return connection

    @staticmethod
    def _to_row(record: CrawlRecord) -> tuple[object, ...]:
        return (
            record.url,
            record.title,
            record.text,
            json.dumps(record.links, ensure_ascii=False),
            json.dumps(record.metadata, ensure_ascii=False),
            record.crawled_at.isoformat(),
            record.status_code,
            record.content_type,
        )

    @staticmethod
    def _row_to_record(row: aiosqlite.Row | tuple[object, ...]) -> CrawlRecord:
        return CrawlRecord(
            url=str(row[0]),
            title=str(row[1]),
            text=str(row[2]),
            links=json.loads(str(row[3])),
            metadata=json.loads(str(row[4])),
            crawled_at=datetime.fromisoformat(str(row[5])),
            status_code=int(str(row[6])),
            content_type=str(row[7]),
        )
