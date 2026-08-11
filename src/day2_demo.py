"""Day 2 demonstration: fetch pages, parse HTML and print statistics."""

import asyncio
import json
import logging
import time
from typing import Any

from .crawler import AsyncCrawler


URLS = [
    "https://example.com",
    "https://www.python.org",
    "https://docs.aiohttp.org/en/stable/",
    "https://www.crummy.com/software/BeautifulSoup/bs4/doc/",
]


async def fetch_pages(urls: list[str]) -> tuple[list[dict[str, Any]], float]:
    """Fetch and parse several pages concurrently."""
    async with AsyncCrawler(max_concurrent=4) as crawler:
        started_at = time.perf_counter()
        results = await asyncio.gather(
            *(crawler.fetch_and_parse(url) for url in urls)
        )
        duration = time.perf_counter() - started_at
    return results, duration


def build_page_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Reduce a full parser result to a readable demonstration summary."""
    return {
        "url": result["url"],
        "status": "error" if result.get("error") else "success",
        "title": result["title"],
        "text_length": len(result["text"]),
        "links_count": len(result["links"]),
        "images_count": len(result["images"]),
        "headings": [heading["text"] for heading in result["headings"][:5]],
        "links_sample": result["links"][:5],
        "error": result.get("error"),
    }


def build_statistics(
    results: list[dict[str, Any]],
    duration: float,
) -> dict[str, int | float]:
    """Calculate aggregate statistics for the demonstration run."""
    successful = sum(result.get("error") is None for result in results)
    return {
        "pages_total": len(results),
        "pages_successful": successful,
        "pages_failed": len(results) - successful,
        "total_text_length": sum(len(result["text"]) for result in results),
        "total_links": sum(len(result["links"]) for result in results),
        "total_images": sum(len(result["images"]) for result in results),
        "elapsed_seconds": round(duration, 3),
    }


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    results, duration = await fetch_pages(URLS)
    report = {
        "pages": [build_page_summary(result) for result in results],
        "statistics": build_statistics(results, duration),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
