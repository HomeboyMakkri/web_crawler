"""Minimal asynchronous storage contract for crawl records."""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import TypeAlias

from .crawl_record import CrawlRecord


RecordInput: TypeAlias = CrawlRecord | dict[str, object]


class DataStorage(ABC):
    """Base lifecycle shared by every crawl-record storage.

    A storage starts open. ``save()``, ``save_many()`` and ``flush()`` may only
    be called while it is open. A successful ``close()`` flushes pending data,
    releases resources and is safe to call again.

    Calling ``close()`` concurrently with another operation is intentionally
    outside this small base contract. Concrete storages may add locking when
    their underlying resource requires it.
    """

    def __init__(self) -> None:
        self._closed = False

    @property
    def closed(self) -> bool:
        """Whether the storage completed its close lifecycle."""
        return self._closed

    async def save(self, data: RecordInput) -> None:
        """Save one record."""
        self._ensure_open()
        record = self._normalize(data)
        await self._save(record)

    async def save_many(self, data: Iterable[RecordInput]) -> None:
        """Save records in iteration order.

        The default implementation uses ``_save()`` repeatedly. A storage with
        native batch support can override ``_save_many()`` without changing the
        public lifecycle contract.
        """
        self._ensure_open()
        records = [self._normalize(item) for item in data]
        await self._save_many(records)

    async def flush(self) -> None:
        """Make all previously accepted records durable."""
        self._ensure_open()
        await self._flush()

    async def close(self) -> None:
        """Flush and release resources; repeated calls are no-ops."""
        if self._closed:
            return

        await self._flush()
        await self._close()
        self._closed = True

    @abstractmethod
    async def _save(self, data: CrawlRecord) -> None:
        """Store one record in the concrete backend."""

    async def _save_many(self, data: Iterable[CrawlRecord]) -> None:
        """Default batch implementation for backends without native batches."""
        for record in data:
            await self._save(record)

    async def _flush(self) -> None:
        """Flush backend buffers when the backend has any."""

    async def _close(self) -> None:
        """Release backend resources when the backend has any."""

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("storage is closed")

    @staticmethod
    def _normalize(data: RecordInput) -> CrawlRecord:
        if isinstance(data, CrawlRecord):
            return data
        if isinstance(data, dict):
            return CrawlRecord.from_dict(data)
        raise ValueError("data must be a CrawlRecord or dictionary")
