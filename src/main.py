"""Day 1 demonstration: sequential versus concurrent page loading."""

import asyncio
import logging
import time

from .crawler import AsyncCrawler


URLS = [
    "https://httpbin.org/delay/1?request=1",
    "https://httpbin.org/delay/1?request=2",
    "https://httpbin.org/delay/1?request=3",
    "https://httpbin.org/delay/1?request=4",
    "https://example.com",
    "https://httpbin.org/status/404",
]


async def load(urls: list[str], max_concurrent: int) -> tuple[dict[str, str], float]:
    """Load URLs and measure only the request workload."""
    async with AsyncCrawler(max_concurrent=max_concurrent) as crawler:
        started_at = time.perf_counter()
        results = await crawler.fetch_urls(urls)
        duration = time.perf_counter() - started_at
    return results, duration


def print_results(results: dict[str, str]) -> None:
    for url, content in results.items():
        if content.startswith("Error:"):
            print(f"ERROR | {url} | {content}")
        else:
            print(f"OK    | {url} | {len(content)} bytes")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    concurrent_results, concurrent_time = await load(URLS, max_concurrent=6)
    _, sequential_time = await load(URLS, max_concurrent=1)

    print("\n--- REQUEST RESULTS ---")
    print_results(concurrent_results)
    print("\n--- SPEED COMPARISON ---")
    print(f"Concurrent (max 6): {concurrent_time:.2f} s")
    print(f"Sequential:        {sequential_time:.2f} s")
    if concurrent_time > 0:
        print(f"Speed-up:          {sequential_time / concurrent_time:.2f}x")


if __name__ == "__main__":
    asyncio.run(main())
