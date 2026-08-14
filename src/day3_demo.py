"""Day 3 demonstration: bounded recursive crawling with live progress."""

import asyncio
import json
import logging
from pathlib import Path

from .crawler import AsyncCrawler
from .result_storage import save_crawl_results


START_URLS = ["https://docs.python.org/3/library/asyncio.html"]
OUTPUT_PATH = Path("day3_results.json")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    async with AsyncCrawler(
        max_concurrent=6,
        limit_per_host=2,
        max_depth=1,
    ) as crawler:
        results = await crawler.crawl(
            START_URLS,
            max_pages=15,
            same_domain_only=True,
            exclude_patterns=[r"/_static/", r"/_sources/", r"\.zip$"],
            show_progress=True,
            progress_interval=0.5,
        )
        statistics = crawler.get_crawl_stats()
        output_path = await save_crawl_results(
            OUTPUT_PATH,
            results=results,
            failed_urls=crawler.failed_urls,
            statistics=statistics,
        )

    print("\nСтатистика обхода:")
    print(json.dumps(statistics, ensure_ascii=False, indent=2))
    print(f"Результаты сохранены в: {output_path.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
