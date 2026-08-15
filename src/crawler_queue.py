"""Priority task queue used by the recursive crawler."""

import asyncio
from dataclasses import dataclass, field
from itertools import count


@dataclass(order=True, frozen=True, slots=True)
class CrawlTask:
    """One scheduled crawl operation with ordering metadata."""

    priority: int
    sequence: int
    url: str = field(compare=False)
    depth: int = field(default=0, compare=False)


class CrawlerQueue:
    """Manage unique URLs and their lifecycle in priority order."""

    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue[CrawlTask] = asyncio.PriorityQueue()
        self._sequence = count()

        self._scheduled_urls: set[str] = set()
        self._queued_urls: set[str] = set()
        self._active_urls: set[str] = set()
        self._processed_urls: set[str] = set()
        self._failed_urls: dict[str, str] = {}
        self._blocked_urls: dict[str, str] = {}

    def add_url(
        self,
        url: str,
        priority: int = 0,
        *,
        depth: int = 0,
    ) -> bool:
        """Schedule a URL once and return whether it was added."""
        normalized_url = self._validate_url(url)
        self._validate_integer(priority, "priority")
        self._validate_depth(depth)

        if normalized_url in self._scheduled_urls:
            return False

        task = CrawlTask(
            priority=priority,
            sequence=next(self._sequence),
            url=normalized_url,
            depth=depth,
        )
        self._queue.put_nowait(task)
        self._scheduled_urls.add(normalized_url)
        self._queued_urls.add(normalized_url)
        return True

    async def get_next(self) -> str | None:
        """Return the next URL, or ``None`` immediately when the queue is empty."""
        try:
            task = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

        self._activate(task)
        return task.url

    async def _wait_for_next_task(self) -> CrawlTask:
        """Wait for a full task used internally by recursive crawl workers."""
        task = await self._queue.get()
        self._activate(task)
        return task

    def _activate(self, task: CrawlTask) -> None:
        """Move one dequeued task into the active lifecycle state."""
        self._queued_urls.remove(task.url)
        self._active_urls.add(task.url)

    def mark_processed(self, url: str) -> None:
        """Mark an active URL as successfully processed."""
        normalized_url = self._validate_url(url)
        self._finish_active(normalized_url)
        self._processed_urls.add(normalized_url)

    def mark_failed(self, url: str, error: str) -> None:
        """Mark an active URL as failed and retain its error message."""
        normalized_url = self._validate_url(url)
        if not isinstance(error, str) or not error.strip():
            raise ValueError("error must be a non-empty string")

        self._finish_active(normalized_url)
        self._failed_urls[normalized_url] = error.strip()

    def mark_blocked(self, url: str, reason: str) -> None:
        """Finish an active URL that policy intentionally prevented fetching."""
        normalized_url = self._validate_url(url)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")

        self._finish_active(normalized_url)
        self._blocked_urls[normalized_url] = reason.strip()

    async def join(self) -> None:
        """Wait until every scheduled task has been marked as finished."""
        await self._queue.join()

    def get_stats(self) -> dict[str, int]:
        """Return a consistent snapshot of queue lifecycle counters."""
        processed = len(self._processed_urls)
        failed = len(self._failed_urls)
        blocked = len(self._blocked_urls)
        return {
            "scheduled": len(self._scheduled_urls),
            "queued": self._queue.qsize(),
            "active": len(self._active_urls),
            "processed": processed,
            "failed": failed,
            "blocked": blocked,
            "completed": processed + failed + blocked,
        }

    @property
    def scheduled_count(self) -> int:
        """Number of unique URLs accepted during this queue's lifetime."""
        return len(self._scheduled_urls)

    @property
    def scheduled_urls(self) -> frozenset[str]:
        return frozenset(self._scheduled_urls)

    @property
    def active_urls(self) -> frozenset[str]:
        return frozenset(self._active_urls)

    @property
    def processed_urls(self) -> frozenset[str]:
        return frozenset(self._processed_urls)

    @property
    def failed_urls(self) -> dict[str, str]:
        return self._failed_urls.copy()

    @property
    def blocked_urls(self) -> dict[str, str]:
        return self._blocked_urls.copy()

    def _finish_active(self, url: str) -> None:
        if url not in self._active_urls:
            raise ValueError(f"URL is not active: {url}")

        self._active_urls.remove(url)
        self._queue.task_done()

    @staticmethod
    def _validate_url(url: str) -> str:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url must be a non-empty string")
        return url.strip()

    @staticmethod
    def _validate_integer(value: int, name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")

    @staticmethod
    def _validate_depth(depth: int) -> None:
        CrawlerQueue._validate_integer(depth, "depth")
        if depth < 0:
            raise ValueError("depth must be greater than or equal to zero")
