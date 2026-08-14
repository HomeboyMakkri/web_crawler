import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.crawler import AsyncCrawler
from src.crawler_queue import CrawlerQueue
from src.url_filter import URLFilter


PageResult = dict[str, Any]
FetchImplementation = Callable[[str], Awaitable[PageResult] | PageResult]


def page(
    url: str,
    links: list[str] | None = None,
    error: str | None = None,
) -> PageResult:
    return {
        "url": url,
        "title": url,
        "text": "content" if error is None else "",
        "links": links or [],
        "metadata": {},
        "images": [],
        "headings": [],
        "tables": [],
        "lists": [],
        "error": error,
    }


def mock_fetch_and_parse(
    crawler: AsyncCrawler,
    *,
    side_effect: FetchImplementation | None = None,
    return_value: PageResult | None = None,
) -> AsyncMock:
    """Install a runtime mock without redefining AsyncCrawler's method type."""
    mock = AsyncMock(side_effect=side_effect, return_value=return_value)
    setattr(crawler, "fetch_and_parse", mock)
    return mock


async def stop_workers(workers: list[asyncio.Task[None]]) -> None:
    for worker in workers:
        worker.cancel()
    await asyncio.gather(*workers, return_exceptions=True)


@pytest.mark.asyncio
async def test_worker_processes_links_up_to_max_depth() -> None:
    root = "https://example.com/"
    child = "https://example.com/child"
    deep = "https://example.com/deep"
    external = "https://external.example/page"
    pages = {
        root: page(root, [child, external]),
        child: page(child, [deep]),
        deep: page(deep),
    }
    crawler = AsyncCrawler()
    mock_fetch_and_parse(crawler, side_effect=lambda url: pages[url])
    queue = CrawlerQueue()
    queue.add_url(root, depth=0)
    url_filter = URLFilter.from_start_urls([root], same_domain_only=True)
    worker = asyncio.create_task(
        crawler._crawl_worker(
            queue,
            url_filter,
            max_depth=1,
            max_pages=10,
        )
    )

    await asyncio.wait_for(queue.join(), timeout=0.2)
    await stop_workers([worker])

    assert crawler.visited_urls == {root, child}
    assert crawler.processed_urls.keys() == {root, child}
    assert crawler.failed_urls == {}
    assert crawler.url_depths == {root: 0, child: 1}
    assert deep not in queue.scheduled_urls
    assert external not in queue.scheduled_urls


@pytest.mark.asyncio
async def test_workers_do_not_process_duplicate_discovered_links() -> None:
    root = "https://example.com/"
    first = "https://example.com/first"
    second = "https://example.com/second"
    shared = "https://example.com/shared"
    pages = {
        root: page(root, [first, second]),
        first: page(first, [shared]),
        second: page(second, [shared]),
        shared: page(shared),
    }

    async def fetch(url: str) -> PageResult:
        await asyncio.sleep(0)
        return pages[url]

    crawler = AsyncCrawler()
    fetch_mock = mock_fetch_and_parse(crawler, side_effect=fetch)
    queue = CrawlerQueue()
    queue.add_url(root)
    url_filter = URLFilter.from_start_urls([root], same_domain_only=True)
    workers = [
        asyncio.create_task(
            crawler._crawl_worker(
                queue,
                url_filter,
                max_depth=2,
                max_pages=10,
            )
        )
        for _ in range(2)
    ]

    await asyncio.wait_for(queue.join(), timeout=0.2)
    await stop_workers(workers)

    assert crawler.visited_urls == {root, first, second, shared}
    assert sum(call.args == (shared,) for call in fetch_mock.await_args_list) == 1
    assert queue.scheduled_count == 4


@pytest.mark.asyncio
async def test_worker_respects_max_pages() -> None:
    root = "https://example.com/"
    links = [f"https://example.com/{index}" for index in range(5)]
    pages = {root: page(root, links)} | {link: page(link) for link in links}
    crawler = AsyncCrawler()
    mock_fetch_and_parse(crawler, side_effect=lambda url: pages[url])
    queue = CrawlerQueue()
    queue.add_url(root)
    worker = asyncio.create_task(
        crawler._crawl_worker(
            queue,
            URLFilter(),
            max_depth=1,
            max_pages=3,
        )
    )

    await asyncio.wait_for(queue.join(), timeout=0.2)
    await stop_workers([worker])

    assert queue.scheduled_count == 3
    assert len(crawler.processed_urls) == 3


@pytest.mark.asyncio
async def test_worker_records_fetch_and_unexpected_errors() -> None:
    fetch_failure = "https://example.com/fetch-failure"
    crash = "https://example.com/crash"

    async def fetch(url: str) -> PageResult:
        if url == fetch_failure:
            return page(url, error="Error: HTTP 500")
        raise RuntimeError("parser crashed")

    crawler = AsyncCrawler()
    mock_fetch_and_parse(crawler, side_effect=fetch)
    queue = CrawlerQueue()
    queue.add_url(fetch_failure)
    queue.add_url(crash)
    worker = asyncio.create_task(
        crawler._crawl_worker(
            queue,
            URLFilter(),
            max_depth=0,
            max_pages=2,
        )
    )

    await asyncio.wait_for(queue.join(), timeout=0.2)
    await stop_workers([worker])

    assert crawler.processed_urls == {}
    assert crawler.failed_urls == {
        fetch_failure: "Error: HTTP 500",
        crash: "RuntimeError: parser crashed",
    }
    assert queue.failed_urls == crawler.failed_urls


@pytest.mark.asyncio
async def test_worker_finishes_task_when_link_filter_crashes() -> None:
    root = "https://example.com/"
    crawler = AsyncCrawler()
    mock_fetch_and_parse(
        crawler,
        return_value=page(root, ["https://example.com/child"]),
    )
    queue = CrawlerQueue()
    queue.add_url(root)
    url_filter = MagicMock(spec=URLFilter)
    url_filter.should_crawl.side_effect = RuntimeError("filter crashed")
    worker = asyncio.create_task(
        crawler._crawl_worker(
            queue,
            url_filter,
            max_depth=1,
            max_pages=10,
        )
    )

    await asyncio.wait_for(queue.join(), timeout=0.2)
    await stop_workers([worker])

    assert crawler.processed_urls == {}
    assert crawler.failed_urls == {root: "RuntimeError: filter crashed"}
    assert queue.get_stats()["active"] == 0


def test_reset_crawl_state_clears_previous_run() -> None:
    crawler = AsyncCrawler()
    crawler.visited_urls.add("https://example.com")
    crawler.processed_urls["https://example.com"] = page("https://example.com")
    crawler.failed_urls["https://failed.example"] = "Timeout"
    crawler.url_depths["https://example.com"] = 0

    crawler._reset_crawl_state()

    assert crawler.visited_urls == set()
    assert crawler.processed_urls == {}
    assert crawler.failed_urls == {}
    assert crawler.url_depths == {}
