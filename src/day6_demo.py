"""Day 6 demonstration: crawl once and persist to three local formats."""

import asyncio
import json
import logging
import socket
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from aiohttp import web

from .composite_storage import CompositeStorage
from .crawl_record import CrawlRecord
from .crawler import AsyncCrawler
from .csv_storage import CSVStorage
from .json_storage import JSONStorage
from .sqlite_storage import SQLiteStorage


OUTPUT_DIR = Path("day6_results")


def create_demo_app() -> web.Application:
    """Create a deterministic three-page site for local crawling."""

    async def index(_: web.Request) -> web.Response:
        return web.Response(
            text="""
            <html><head><title>Day 6 demo</title></head><body>
              <h1>Storage demonstration</h1>
              <a href="/json">JSON page</a>
              <a href="/database">Database page</a>
            </body></html>
            """,
            content_type="text/html",
        )

    async def json_page(_: web.Request) -> web.Response:
        return web.Response(
            text=(
                "<html><head><title>JSON Lines</title></head>"
                "<body><p>Stored as one JSON object per line.</p></body></html>"
            ),
            content_type="text/html",
        )

    async def database_page(_: web.Request) -> web.Response:
        return web.Response(
            text=(
                "<html><head><title>SQLite</title></head>"
                "<body><p>Stored in an indexed SQLite table.</p></body></html>"
            ),
            content_type="text/html",
        )

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/json", json_page)
    app.router.add_get("/database", database_page)
    return app


async def run_demo(
    *,
    output: Callable[[str], None] = print,
    output_dir: str | Path = OUTPUT_DIR,
) -> dict[str, Any]:
    """Crawl the local site once and verify all three persisted formats."""
    if not callable(output):
        raise ValueError("output must be callable")

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_storage = JSONStorage(directory / "pages.jsonl")
    csv_storage = CSVStorage(directory / "pages.csv")
    sqlite_storage = SQLiteStorage(directory / "pages.db", batch_size=10)
    composite = CompositeStorage(
        [json_storage, csv_storage, sqlite_storage]
    )

    app = create_demo_app()
    runner = web.AppRunner(app)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = int(listener.getsockname()[1])
    base_url = f"http://127.0.0.1:{port}"

    try:
        await runner.setup()
        site = web.SockSite(runner, listener)
        await site.start()
        output(f"Локальный демонстрационный сайт Day 6: {base_url}")

        async with AsyncCrawler(
            max_concurrent=3,
            max_depth=1,
            storage=composite,
        ) as crawler:
            results = await crawler.crawl(
                [base_url],
                max_pages=3,
                same_domain_only=True,
            )
            await composite.flush()

            json_records = [
                record
                async for record in json_storage.read_records()
            ]
            csv_records = [
                _record_to_json(record)
                for record in await csv_storage.read_records()
            ]
            sqlite_records = [
                _record_to_json(record)
                for record in await sqlite_storage.read_records()
            ]
            manager_stats = (
                crawler.storage_manager.get_stats()
                if crawler.storage_manager is not None
                else {}
            )
            summary: dict[str, Any] = {
                "base_url": base_url,
                "processed_urls": sorted(results),
                "failed_urls": dict(crawler.failed_urls),
                "crawl_stats": crawler.get_crawl_stats(),
                "storage_stats": {
                    "composite": manager_stats,
                    "backends": composite.get_stats(),
                },
                "stored_counts": {
                    "json": len(json_records),
                    "csv": len(csv_records),
                    "sqlite": len(sqlite_records),
                },
                "readback": {
                    "json": json_records,
                    "csv": csv_records,
                    "sqlite": sqlite_records,
                },
                "paths": {
                    "json": str(json_storage.path.resolve()),
                    "csv": str(csv_storage.path.resolve()),
                    "sqlite": str(sqlite_storage.path.resolve()),
                },
            }
    finally:
        await runner.cleanup()
        if listener.fileno() != -1:
            listener.close()

    output("")
    output("Итоговая статистика Day 6:")
    output(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def _record_to_json(record: CrawlRecord) -> dict[str, object]:
    result = record.to_dict()
    crawled_at = result["crawled_at"]
    if isinstance(crawled_at, datetime):
        result["crawled_at"] = crawled_at.isoformat()
    return result


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    await run_demo()


if __name__ == "__main__":
    asyncio.run(main())
