"""Day 6 demonstration: crawl once and persist to three local formats."""

import asyncio
import json
import logging
import socket
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import aiofiles
from aiohttp import web

from .composite_storage import CompositeStorage
from .crawl_record import CrawlRecord
from .crawler import AsyncCrawler
from .csv_storage import CSVStorage
from .json_storage import JSONStorage
from .sqlite_storage import SQLiteStorage


OUTPUT_DIR = Path("day6_results")
DEMO_OUTPUT_FILENAMES: Final[tuple[str, ...]] = (
    "pages.jsonl",
    "pages.json",
    "pages.csv",
    "pages.db",
    "pages.db-wal",
    "pages.db-shm",
    "pages.db-journal",
)
CRAWL_RECORD_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "url",
        "title",
        "text",
        "links",
        "metadata",
        "crawled_at",
        "status_code",
        "content_type",
    }
)


class StorageIntegrityError(RuntimeError):
    """Persisted backends do not represent the same successful crawl."""


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
    reset_output: bool = True,
) -> dict[str, Any]:
    """Crawl the local site once and verify all three persisted formats."""
    if not callable(output):
        raise ValueError("output must be callable")
    if not isinstance(reset_output, bool):
        raise ValueError("reset_output must be a boolean")

    directory = _prepare_output_directory(
        Path(output_dir),
        reset_output=reset_output,
    )
    json_storage = JSONStorage(directory / "pages.jsonl")
    csv_storage = CSVStorage(directory / "pages.csv")
    sqlite_storage = SQLiteStorage(directory / "pages.db", batch_size=10)
    composite = CompositeStorage(
        [json_storage, csv_storage, sqlite_storage]
    )

    app = create_demo_app()
    runner = web.AppRunner(app)
    listener: socket.socket | None = None
    crawler = AsyncCrawler(
        max_concurrent=3,
        max_depth=1,
        storage=composite,
    )

    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        listener.setblocking(False)
        port = int(listener.getsockname()[1])
        base_url = f"http://127.0.0.1:{port}"

        await runner.setup()
        site = web.SockSite(runner, listener)
        await site.start()
        output(f"Локальный демонстрационный сайт Day 6: {base_url}")

        async with crawler:
            results = await crawler.crawl(
                [base_url],
                max_pages=3,
                same_domain_only=True,
            )
            await composite.flush()
            pretty_path = directory / "pages.json"
            try:
                await json_storage.export_pretty(pretty_path)
            except Exception as error:
                raise StorageIntegrityError(
                    "backend=pretty_json export failed: "
                    f"{type(error).__name__}: {error}"
                ) from error

            try:
                jsonl_records = [
                    record
                    async for record in json_storage.read_records()
                ]
            except Exception as error:
                raise _backend_read_error("jsonl", error) from error
            try:
                pretty_records = await _read_pretty_records(pretty_path)
            except StorageIntegrityError:
                raise
            except Exception as error:
                raise _backend_read_error("pretty_json", error) from error
            try:
                csv_records = [
                    _record_to_json(record)
                    for record in await csv_storage.read_records()
                ]
            except Exception as error:
                raise _backend_read_error("csv", error) from error
            try:
                sqlite_records = [
                    _record_to_json(record)
                    for record in await sqlite_storage.read_records()
                ]
            except Exception as error:
                raise _backend_read_error("sqlite", error) from error
            manager_stats = (
                crawler.storage_manager.get_stats()
                if crawler.storage_manager is not None
                else {}
            )
            records_by_backend = {
                "jsonl": jsonl_records,
                "pretty_json": pretty_records,
                "csv": csv_records,
                "sqlite": sqlite_records,
            }
            integrity_verified = verify_storage_integrity(
                results,
                records_by_backend,
            )
            processed_pages = len(results)
            failed_saves = manager_stats.get("failed_saves", 0)
            summary: dict[str, Any] = {
                "base_url": base_url,
                "processed_urls": sorted(results),
                "processed_pages": processed_pages,
                "unsaved_pages": failed_saves,
                "failed_urls": dict(crawler.failed_urls),
                "crawl_stats": crawler.get_crawl_stats(),
                "storage_stats": {
                    "composite": manager_stats,
                    "backends": composite.get_stats(),
                },
                "stored_counts": {
                    name: len(records)
                    for name, records in records_by_backend.items()
                },
                "integrity_verified": integrity_verified,
                "readback": records_by_backend,
                "paths": {
                    "jsonl": str(json_storage.path.resolve()),
                    "pretty_json": str(pretty_path.resolve()),
                    "csv": str(csv_storage.path.resolve()),
                    "sqlite": str(sqlite_storage.path.resolve()),
                },
            }
    finally:
        try:
            await crawler.close()
        finally:
            try:
                await runner.cleanup()
            finally:
                if listener is not None and listener.fileno() != -1:
                    listener.close()

    output("")
    output("Итоговая статистика Day 6:")
    output(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def _prepare_output_directory(
    directory: Path,
    *,
    reset_output: bool,
) -> Path:
    """Create the demo directory and optionally remove only known outputs."""
    directory.mkdir(parents=True, exist_ok=True)
    if reset_output:
        for filename in DEMO_OUTPUT_FILENAMES:
            try:
                (directory / filename).unlink()
            except FileNotFoundError:
                pass
    return directory


def _backend_read_error(
    backend: str,
    error: Exception,
) -> StorageIntegrityError:
    return StorageIntegrityError(
        f"backend={backend} read failed: {type(error).__name__}: {error}"
    )


async def _read_pretty_records(path: Path) -> list[dict[str, object]]:
    file = await aiofiles.open(path, "r", encoding="utf-8")
    try:
        decoded = json.loads(await file.read())
    finally:
        await file.close()

    if not isinstance(decoded, list):
        raise StorageIntegrityError(
            "backend=pretty_json must contain a JSON array"
        )

    records: list[dict[str, object]] = []
    for index, record in enumerate(decoded):
        if not isinstance(record, dict):
            raise StorageIntegrityError(
                "backend=pretty_json "
                f"record_index={index} must contain an object"
            )
        records.append(record)
    return records


def verify_storage_integrity(
    processed_urls: Iterable[str],
    records_by_backend: Mapping[str, list[dict[str, object]]],
) -> bool:
    """Verify that every backend contains the same complete crawl records."""
    if not records_by_backend:
        raise StorageIntegrityError("no storage backends were provided")

    expected_urls = set(processed_urls)
    indexed_backends: dict[str, dict[str, dict[str, object]]] = {}
    for backend, records in records_by_backend.items():
        indexed: dict[str, dict[str, object]] = {}
        for index, record in enumerate(records):
            fields = set(record)
            if fields != CRAWL_RECORD_FIELDS:
                missing = sorted(CRAWL_RECORD_FIELDS - fields)
                unexpected = sorted(fields - CRAWL_RECORD_FIELDS)
                raise StorageIntegrityError(
                    f"backend={backend} record_index={index} schema mismatch: "
                    f"missing={missing} unexpected={unexpected}"
                )

            url = record["url"]
            if not isinstance(url, str) or not url:
                raise StorageIntegrityError(
                    f"backend={backend} record_index={index} has invalid url"
                )
            if url in indexed:
                raise StorageIntegrityError(
                    f"backend={backend} contains duplicate url={url}"
                )
            indexed[url] = record

        actual_urls = set(indexed)
        if actual_urls != expected_urls:
            raise StorageIntegrityError(
                f"backend={backend} URL mismatch: "
                f"missing={sorted(expected_urls - actual_urls)} "
                f"unexpected={sorted(actual_urls - expected_urls)}"
            )
        indexed_backends[backend] = indexed

    reference_backend, reference_records = next(iter(indexed_backends.items()))
    for backend, records in indexed_backends.items():
        if backend == reference_backend:
            continue
        for url in sorted(expected_urls):
            expected = reference_records[url]
            actual = records[url]
            for field in sorted(CRAWL_RECORD_FIELDS):
                if actual[field] != expected[field]:
                    raise StorageIntegrityError(
                        f"backend={backend} url={url} field={field} mismatch: "
                        f"expected={expected[field]!r} actual={actual[field]!r}"
                    )

    return True


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
