"""Asynchronous JSON storage for completed crawl runs."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import aiofiles


async def save_crawl_results(
    path: str | Path,
    *,
    results: dict[str, dict[str, Any]],
    failed_urls: dict[str, str],
    statistics: Mapping[str, object],
) -> Path:
    """Serialize a complete crawl report without blocking on file writes."""
    report = {
        "results": results,
        "failed_urls": failed_urls,
        "statistics": dict(statistics),
    }
    return await _write_json(path, report)


async def save_error_report(
    path: str | Path,
    *,
    errors: Mapping[str, Mapping[str, object]],
    statistics: Mapping[str, object],
) -> Path:
    """Asynchronously save terminal crawler errors and their statistics."""
    report = {
        "errors": {
            url: dict(error)
            for url, error in errors.items()
        },
        "statistics": dict(statistics),
    }
    return await _write_json(path, report)


async def _write_json(
    path: str | Path,
    payload: Mapping[str, object],
) -> Path:
    output_path = Path(path)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)

    async with aiofiles.open(output_path, "w", encoding="utf-8") as file:
        await file.write(serialized)

    return output_path
