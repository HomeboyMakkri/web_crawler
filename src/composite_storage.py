"""Fan-out storage that writes each crawl record to several backends."""

import asyncio
from collections.abc import Iterable

from .crawl_record import CrawlRecord
from .data_storage import DataStorage
from .storage_manager import StorageManager, StorageManagerStats


class CompositeStorageError(Exception):
    """One or more child storages failed an aggregate operation."""

    def __init__(self, operation: str, failed_storages: list[str]) -> None:
        self.operation = operation
        self.failed_storages = tuple(failed_storages)
        names = ", ".join(failed_storages)
        super().__init__(f"{operation} failed for storages: {names}")


class CompositeStorage(DataStorage):
    """Save every record to multiple independently protected storages."""

    def __init__(self, storages: Iterable[DataStorage]) -> None:
        children = tuple(storages)
        if not children:
            raise ValueError("storages must contain at least one DataStorage")
        if any(not isinstance(storage, DataStorage) for storage in children):
            raise ValueError("storages must contain only DataStorage instances")
        if len({id(storage) for storage in children}) != len(children):
            raise ValueError("the same storage instance cannot be added twice")

        super().__init__()
        self._storages = children
        self._names = self._build_names(children)
        self._managers = tuple(StorageManager(storage) for storage in children)

    @property
    def storages(self) -> tuple[DataStorage, ...]:
        """Return the configured child backends in fan-out order."""
        return self._storages

    def get_stats(self) -> dict[str, StorageManagerStats]:
        """Return per-backend save statistics."""
        return {
            name: manager.get_stats()
            for name, manager in zip(
                self._names,
                self._managers,
                strict=True,
            )
        }

    async def _save(self, data: CrawlRecord) -> None:
        results = await asyncio.gather(
            *(manager.save(data) for manager in self._managers)
        )
        failed = [
            name
            for name, saved in zip(self._names, results, strict=True)
            if not saved
        ]
        if failed:
            raise CompositeStorageError("save", failed)

    async def _flush(self) -> None:
        results = await asyncio.gather(
            *(storage.flush() for storage in self._storages),
            return_exceptions=True,
        )
        self._raise_lifecycle_errors("flush", results)

    async def close(self) -> None:
        """Attempt to close every child even when one backend fails."""
        if self.closed:
            return

        results = await asyncio.gather(
            *(storage.close() for storage in self._storages),
            return_exceptions=True,
        )
        self._raise_lifecycle_errors("close", results)
        self._closed = True

    def _raise_lifecycle_errors(
        self,
        operation: str,
        results: list[None | BaseException],
    ) -> None:
        failed = [
            name
            for name, result in zip(self._names, results, strict=True)
            if isinstance(result, BaseException)
        ]
        if failed:
            raise CompositeStorageError(operation, failed)

    @staticmethod
    def _build_names(storages: tuple[DataStorage, ...]) -> tuple[str, ...]:
        counts: dict[str, int] = {}
        names: list[str] = []
        for storage in storages:
            base_name = type(storage).__name__
            number = counts.get(base_name, 0) + 1
            counts[base_name] = number
            names.append(base_name if number == 1 else f"{base_name}#{number}")
        return tuple(names)
