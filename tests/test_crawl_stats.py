import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.crawler import AsyncCrawler


def page(
    url: str,
    *,
    links: list[str] | None = None,
    text: str = "content",
    images: list[dict[str, str]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "url": url,
        "title": url,
        "text": "" if error else text,
        "links": links or [],
        "metadata": {},
        "images": images or [],
        "headings": [],
        "tables": [],
        "lists": [],
        "error": error,
    }


def test_stats_are_zero_before_a_crawl() -> None:
    stats = AsyncCrawler().get_crawl_stats()

    assert stats == {
        "pages_scheduled": 0,
        "pages_queued": 0,
        "pages_active": 0,
        "pages_successful": 0,
        "pages_failed": 0,
        "pages_completed": 0,
        "active_requests": 0,
        "max_depth_reached": 0,
        "total_text_length": 0,
        "total_links": 0,
        "total_images": 0,
        "elapsed_seconds": 0.0,
        "pages_per_second": 0.0,
    }


@pytest.mark.asyncio
async def test_stats_aggregate_successes_failures_content_and_depth() -> None:
    root = "https://example.com/"
    child = "https://example.com/child"
    failed = "https://example.com/failed"
    pages = {
        root: page(
            root,
            links=[child, failed],
            text="root",
            images=[{"src": "image.png", "alt": "image"}],
        ),
        child: page(child, text="child"),
        failed: page(failed, error="Error: HTTP 503"),
    }
    crawler = AsyncCrawler(max_concurrent=2)
    setattr(crawler, "fetch_and_parse", AsyncMock(side_effect=lambda url: pages[url]))

    await crawler.crawl([root], max_depth=1)
    stats = crawler.get_crawl_stats()

    assert stats["pages_scheduled"] == 3
    assert stats["pages_queued"] == 0
    assert stats["pages_active"] == 0
    assert stats["pages_successful"] == 2
    assert stats["pages_failed"] == 1
    assert stats["pages_completed"] == 3
    assert stats["active_requests"] == 0
    assert stats["max_depth_reached"] == 1
    assert stats["total_text_length"] == 9
    assert stats["total_links"] == 2
    assert stats["total_images"] == 1
    assert stats["elapsed_seconds"] >= 0
    assert stats["pages_per_second"] > 0


@pytest.mark.asyncio
async def test_crawl_reporter_emits_live_and_final_progress() -> None:
    root = "https://example.com/"
    messages: list[str] = []

    async def slow_fetch(url: str) -> dict[str, Any]:
        await asyncio.sleep(0.01)
        return page(url)

    crawler = AsyncCrawler()
    setattr(crawler, "fetch_and_parse", AsyncMock(side_effect=slow_fetch))

    await crawler.crawl(
        [root],
        max_depth=0,
        show_progress=True,
        progress_interval=0.001,
        progress_output=messages.append,
    )

    assert any(message.startswith("Прогресс:") for message in messages)
    assert messages[-1].startswith("Итог: обработано 1/1")


@pytest.mark.asyncio
async def test_crawl_does_not_report_when_progress_is_disabled() -> None:
    messages: list[str] = []
    crawler = AsyncCrawler()
    setattr(
        crawler,
        "fetch_and_parse",
        AsyncMock(return_value=page("https://example.com/")),
    )

    await crawler.crawl(
        ["https://example.com/"],
        max_depth=0,
        progress_output=messages.append,
    )

    assert messages == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"show_progress": "yes"}, "show_progress"),
        ({"progress_interval": 0}, "progress_interval"),
        ({"progress_output": "stdout"}, "progress_output"),
    ],
)
@pytest.mark.asyncio
async def test_crawl_validates_progress_options(kwargs: dict, message: str) -> None:
    crawler = AsyncCrawler()

    with pytest.raises(ValueError, match=message):
        await crawler.crawl(["https://example.com/"], max_depth=0, **kwargs)
