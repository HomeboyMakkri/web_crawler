import asyncio
from unittest.mock import AsyncMock

import pytest

from src.crawler import AsyncCrawler


def page(url: str, links: list[str] | None = None, error: str | None = None) -> dict:
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


@pytest.mark.asyncio
async def test_crawl_discovers_pages_and_obeys_default_depth() -> None:
    root = "https://example.com/"
    child = "https://example.com/child"
    deep = "https://example.com/deep"
    too_deep = "https://example.com/too-deep"
    pages = {
        root: page(root, [child]),
        child: page(child, [deep]),
        deep: page(deep, [too_deep]),
        too_deep: page(too_deep),
    }
    crawler = AsyncCrawler(max_concurrent=3, max_depth=2)
    crawler.fetch_and_parse = AsyncMock(side_effect=lambda url: pages[url])

    results = await crawler.crawl([root], max_pages=10)

    assert results.keys() == {root, child, deep}
    assert crawler.visited_urls == {root, child, deep}
    assert crawler.url_depths == {root: 0, child: 1, deep: 2}
    assert too_deep not in crawler.visited_urls


@pytest.mark.asyncio
async def test_crawl_call_can_override_depth_and_apply_patterns() -> None:
    root = "https://example.com/"
    docs = "https://example.com/docs/intro"
    private = "https://example.com/docs/private/page"
    article = "https://example.com/article"
    external = "https://external.example/docs/page"
    pages = {
        root: page(root, [docs, private, article, external]),
        docs: page(docs),
    }
    crawler = AsyncCrawler(max_depth=5)
    crawler.fetch_and_parse = AsyncMock(side_effect=lambda url: pages[url])

    results = await crawler.crawl(
        [root],
        max_depth=1,
        same_domain_only=True,
        include_patterns=[r"/docs/"],
        exclude_patterns=[r"/private/"],
    )

    assert results.keys() == {root, docs}


@pytest.mark.asyncio
async def test_crawl_allows_all_start_domains_and_respects_max_pages() -> None:
    first_root = "https://one.example/"
    second_root = "https://two.example/"
    first_child = "https://one.example/child"
    second_child = "https://two.example/child"
    pages = {
        first_root: page(first_root, [first_child]),
        second_root: page(second_root, [second_child]),
        first_child: page(first_child),
        second_child: page(second_child),
    }
    crawler = AsyncCrawler(max_concurrent=2)
    crawler.fetch_and_parse = AsyncMock(side_effect=lambda url: pages[url])

    results = await crawler.crawl(
        [first_root, second_root],
        max_pages=3,
        max_depth=1,
        same_domain_only=True,
    )

    assert len(results) == 3
    assert {first_root, second_root} <= results.keys()
    assert crawler._crawl_queue is not None
    assert crawler._crawl_queue.scheduled_count == 3


@pytest.mark.asyncio
async def test_crawl_returns_successes_and_keeps_failures_in_state() -> None:
    root = "https://example.com/"
    failed = "https://example.com/failed"
    crawler = AsyncCrawler()
    crawler.fetch_and_parse = AsyncMock(
        side_effect=[
            page(root, [failed]),
            page(failed, error="Error: HTTP 503"),
        ]
    )

    results = await crawler.crawl([root], max_depth=1)

    assert results == {root: page(root, [failed])}
    assert crawler.failed_urls == {failed: "Error: HTTP 503"}
    assert crawler.visited_urls == {root, failed}


@pytest.mark.asyncio
async def test_crawl_resets_state_between_runs() -> None:
    first = "https://first.example/"
    second = "https://second.example/"
    crawler = AsyncCrawler()
    crawler.fetch_and_parse = AsyncMock(side_effect=lambda url: page(url))

    await crawler.crawl([first], max_depth=0)
    second_results = await crawler.crawl([second], max_depth=0)

    assert second_results.keys() == {second}
    assert crawler.visited_urls == {second}
    assert crawler.processed_urls.keys() == {second}
    assert crawler.failed_urls == {}


@pytest.mark.asyncio
async def test_same_crawler_rejects_overlapping_crawl_calls() -> None:
    root = "https://example.com/"
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_fetch(url: str) -> dict:
        entered.set()
        await release.wait()
        return page(url)

    crawler = AsyncCrawler()
    crawler.fetch_and_parse = AsyncMock(side_effect=slow_fetch)
    first_crawl = asyncio.create_task(crawler.crawl([root], max_depth=0))
    await entered.wait()

    with pytest.raises(RuntimeError, match="already running"):
        await crawler.crawl([root], max_depth=0)

    release.set()
    await first_crawl


@pytest.mark.parametrize(
    ("start_urls", "max_pages", "max_depth", "message"),
    [
        ([], 10, 1, "start_urls"),
        (["relative/path"], 10, 1, "start URL"),
        (["https://example.com"], 0, 1, "max_pages"),
        (["https://example.com"], 10, -1, "max_depth"),
        (["https://example.com"], 10, True, "max_depth"),
    ],
)
@pytest.mark.asyncio
async def test_crawl_validates_arguments(
    start_urls: list[str],
    max_pages: int,
    max_depth: int,
    message: str,
) -> None:
    crawler = AsyncCrawler()

    with pytest.raises(ValueError, match=message):
        await crawler.crawl(
            start_urls,
            max_pages=max_pages,
            max_depth=max_depth,
        )


@pytest.mark.parametrize("max_depth", [-1, True, 1.5])
def test_constructor_validates_default_max_depth(max_depth) -> None:
    with pytest.raises(ValueError, match="max_depth"):
        AsyncCrawler(max_depth=max_depth)
