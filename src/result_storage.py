"""Asynchronous JSON storage for completed crawl runs."""

import json
from pathlib import Path
from typing import Any

import aiofiles


async def save_crawl_results(
    path: str | Path,
    *,
    results: dict[str, dict[str, Any]],
    failed_urls: dict[str, str],
    statistics: dict[str, int | float],
) -> Path:
    """Serialize a complete crawl report without blocking on file writes."""
    output_path = Path(path)
    report = {
        "results": results,
        "failed_urls": failed_urls,
        "statistics": statistics,
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2)

    async with aiofiles.open(output_path, "w", encoding="utf-8") as file:
        await file.write(serialized)

    return output_path
