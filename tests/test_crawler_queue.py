import asyncio

import pytest

from src.crawler_queue import CrawlerQueue


@pytest.mark.asyncio
async def test_queue_returns_tasks_by_priority_and_fifo_order() -> None:
    queue = CrawlerQueue()
    queue.add_url("https://example.com/low", priority=10, depth=2)
    queue.add_url("https://example.com/first", priority=0, depth=1)
    queue.add_url("https://example.com/high", priority=-1, depth=0)
    queue.add_url("https://example.com/second", priority=0, depth=1)

    tasks = []
    for _ in range(4):
        task = await queue._wait_for_next_task()
        tasks.append(task)
        queue.mark_processed(task.url)

    assert [task.url for task in tasks] == [
        "https://example.com/high",
        "https://example.com/first",
        "https://example.com/second",
        "https://example.com/low",
    ]
    assert [task.depth for task in tasks] == [0, 1, 1, 2]


def test_add_url_rejects_duplicates() -> None:
    queue = CrawlerQueue()

    assert queue.add_url("https://example.com/page") is True
    assert queue.add_url("https://example.com/page") is False
    assert queue.add_url("  https://example.com/page  ") is False

    assert queue.scheduled_urls == {"https://example.com/page"}
    assert queue.get_stats() == {
        "scheduled": 1,
        "queued": 1,
        "active": 0,
        "processed": 0,
        "failed": 0,
        "blocked": 0,
        "completed": 0,
    }


@pytest.mark.asyncio
async def test_processed_task_updates_lifecycle_and_unblocks_join() -> None:
    queue = CrawlerQueue()
    queue.add_url("https://example.com/page")

    url = await queue.get_next()
    assert url is not None
    join_task = asyncio.create_task(queue.join())
    await asyncio.sleep(0)

    assert join_task.done() is False
    assert queue.active_urls == {url}
    assert queue.get_stats()["active"] == 1

    queue.mark_processed(url)
    await asyncio.wait_for(join_task, timeout=0.1)

    assert queue.active_urls == set()
    assert queue.processed_urls == {url}
    assert queue.get_stats() == {
        "scheduled": 1,
        "queued": 0,
        "active": 0,
        "processed": 1,
        "failed": 0,
        "blocked": 0,
        "completed": 1,
    }


@pytest.mark.asyncio
async def test_failed_task_retains_error_and_updates_stats() -> None:
    queue = CrawlerQueue()
    queue.add_url("https://example.com/failure")

    url = await queue.get_next()
    assert url is not None
    queue.mark_failed(url, "Timeout")
    await asyncio.wait_for(queue.join(), timeout=0.1)

    assert queue.failed_urls == {url: "Timeout"}
    assert queue.get_stats() == {
        "scheduled": 1,
        "queued": 0,
        "active": 0,
        "processed": 0,
        "failed": 1,
        "blocked": 0,
        "completed": 1,
    }


@pytest.mark.asyncio
async def test_blocked_task_is_completed_without_becoming_failed() -> None:
    queue = CrawlerQueue()
    queue.add_url("https://example.com/private")

    url = await queue.get_next()
    assert url is not None
    queue.mark_blocked(url, "Blocked by robots.txt")
    await asyncio.wait_for(queue.join(), timeout=0.1)

    assert queue.blocked_urls == {url: "Blocked by robots.txt"}
    assert queue.failed_urls == {}
    assert queue.get_stats() == {
        "scheduled": 1,
        "queued": 0,
        "active": 0,
        "processed": 0,
        "failed": 0,
        "blocked": 1,
        "completed": 1,
    }


@pytest.mark.asyncio
async def test_get_next_returns_none_without_waiting_when_queue_is_empty() -> None:
    queue = CrawlerQueue()

    async with asyncio.timeout(0.1):
        assert await queue.get_next() is None

    queue.add_url("https://example.com/later", depth=3)
    url = await queue.get_next()
    assert url == "https://example.com/later"
    queue.mark_processed(url)


@pytest.mark.asyncio
async def test_internal_worker_method_waits_and_preserves_task_metadata() -> None:
    queue = CrawlerQueue()
    waiting_worker = asyncio.create_task(queue._wait_for_next_task())
    await asyncio.sleep(0)

    assert waiting_worker.done() is False

    queue.add_url("https://example.com/later", priority=4, depth=3)
    task = await asyncio.wait_for(waiting_worker, timeout=0.1)
    queue.mark_processed(task.url)

    assert task.url == "https://example.com/later"
    assert task.priority == 4
    assert task.depth == 3


@pytest.mark.parametrize(
    ("url", "priority", "depth", "message"),
    [
        ("", 0, 0, "url"),
        ("https://example.com", True, 0, "priority"),
        ("https://example.com", 0, -1, "depth"),
        ("https://example.com", 0, 1.5, "depth"),
    ],
)
def test_add_url_validates_arguments(
    url: str,
    priority: int,
    depth: int,
    message: str,
) -> None:
    queue = CrawlerQueue()

    with pytest.raises(ValueError, match=message):
        queue.add_url(url, priority, depth=depth)


@pytest.mark.asyncio
async def test_only_active_tasks_can_be_finished() -> None:
    queue = CrawlerQueue()
    queue.add_url("https://example.com/queued")

    with pytest.raises(ValueError, match="not active"):
        queue.mark_processed("https://example.com/queued")

    url = await queue.get_next()
    assert url is not None
    queue.mark_processed(url)

    with pytest.raises(ValueError, match="not active"):
        queue.mark_failed(url, "Second completion")

    with pytest.raises(ValueError, match="not active"):
        queue.mark_blocked(url, "Second completion")
