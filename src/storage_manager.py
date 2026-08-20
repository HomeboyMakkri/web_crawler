"""Fault-tolerant orchestration around a crawl-record storage."""

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from typing import TypedDict

from .crawl_record import CrawlRecord
from .data_storage import DataStorage
from .retry_strategy import RetryStrategy


logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]
ErrorType = type[Exception]


class StorageManagerStats(TypedDict):
    saved_records: int
    failed_saves: int
    retried_saves: int


class StorageManager:
    """Save records without allowing backend failures to stop the crawler.

    ``failed_saves`` counts records that were not saved after all attempts,
    while ``retried_saves`` counts additional attempts scheduled after the
    initial call.
    """

    def __init__(
        self,
        storage: DataStorage,
        *,
        max_retries: int = 3,
        retry_on: list[ErrorType] | tuple[ErrorType, ...] | None = None,
        base_delay: float = 0.1,
        backoff_factor: float = 2.0,
        max_delay: float = 5.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if not isinstance(storage, DataStorage):
            raise ValueError("storage must implement DataStorage")
        if not callable(sleep):
            raise ValueError("sleep must be callable")

        self._storage = storage
        self._sleep = sleep
        self._saved_records = 0
        self._failed_saves = 0
        self._retried_saves = 0
        storage_errors = (
            [OSError, sqlite3.OperationalError]
            if retry_on is None
            else retry_on
        )
        self._retry_strategy = RetryStrategy(
            max_retries=max_retries,
            retry_on=storage_errors,
            base_delay=base_delay,
            backoff_factor=backoff_factor,
            max_delay=max_delay,
            sleep=self._sleep_before_retry,
        )

    async def save(self, record: CrawlRecord) -> bool:
        """Return whether one record was eventually saved.

        Ordinary storage exceptions are converted into ``False``. Task
        cancellation remains a control-flow signal and is intentionally
        propagated.
        """
        try:
            await self._retry_strategy.execute_with_retry(
                self._storage.save,
                record,
            )
        except Exception as error:
            self._failed_saves += 1
            logger.error(
                "Storage save failed: url=%s type=%s error=%s",
                record.url,
                type(error).__name__,
                error,
            )
            return False

        self._saved_records += 1
        return True

    def get_stats(self) -> StorageManagerStats:
        """Return a detached lifetime statistics snapshot."""
        return {
            "saved_records": self._saved_records,
            "failed_saves": self._failed_saves,
            "retried_saves": self._retried_saves,
        }

    async def _sleep_before_retry(self, delay: float) -> None:
        self._retried_saves += 1
        await self._sleep(delay)
