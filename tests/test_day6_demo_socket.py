import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from src.day6_demo import run_demo


BACKEND_NAMES = {"jsonl", "pretty_json", "csv", "sqlite"}
EXPECTED_COUNTS = {name: 3 for name in BACKEND_NAMES}


def records_by_url(
    records: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    return {str(record["url"]): record for record in records}


def assert_successful_summary(summary: dict[str, Any]) -> None:
    base_url = summary["base_url"]
    expected_urls = {
        base_url,
        f"{base_url}/json",
        f"{base_url}/database",
    }

    assert summary["processed_pages"] == 3
    assert set(summary["processed_urls"]) == expected_urls
    assert summary["failed_urls"] == {}
    assert summary["unsaved_pages"] == 0
    assert summary["stored_counts"] == EXPECTED_COUNTS
    assert summary["integrity_verified"] is True

    crawl_stats = summary["crawl_stats"]
    assert crawl_stats["pages_successful"] == 3
    assert crawl_stats["pages_failed"] == 0

    storage_stats = summary["storage_stats"]
    assert storage_stats["composite"] == {
        "saved_records": 3,
        "failed_saves": 0,
        "retried_saves": 0,
    }
    assert set(storage_stats["backends"]) == {
        "JSONStorage",
        "CSVStorage",
        "SQLiteStorage",
    }
    assert all(
        stats == {
            "saved_records": 3,
            "failed_saves": 0,
            "retried_saves": 0,
        }
        for stats in storage_stats["backends"].values()
    )

    readback = summary["readback"]
    assert set(readback) == BACKEND_NAMES
    assert all(len(records) == 3 for records in readback.values())

    jsonl_by_url = records_by_url(readback["jsonl"])
    assert set(jsonl_by_url) == expected_urls
    assert {
        url: record["title"]
        for url, record in jsonl_by_url.items()
    } == {
        base_url: "Day 6 demo",
        f"{base_url}/json": "JSON Lines",
        f"{base_url}/database": "SQLite",
    }

    compared_fields = (
        "title",
        "text",
        "status_code",
        "content_type",
    )
    for backend, records in readback.items():
        backend_by_url = records_by_url(records)
        assert set(backend_by_url) == expected_urls
        for url in expected_urls:
            assert {
                field: backend_by_url[url][field]
                for field in compared_fields
            } == {
                field: jsonl_by_url[url][field]
                for field in compared_fields
            }, f"backend={backend} url={url}"

    paths = summary["paths"]
    assert set(paths) == BACKEND_NAMES
    assert {Path(path).name for path in paths.values()} == {
        "pages.jsonl",
        "pages.json",
        "pages.csv",
        "pages.db",
    }
    assert all(Path(path).is_file() for path in paths.values())


def assert_demo_server_is_closed(base_url: str) -> None:
    parsed = urlsplit(base_url)
    assert parsed.hostname is not None
    assert parsed.port is not None

    with pytest.raises(OSError):
        socket.create_connection(
            (parsed.hostname, parsed.port),
            timeout=0.1,
        )


@pytest.mark.socket
async def test_demo_exercises_complete_day6_pipeline(tmp_path: Path) -> None:
    messages: list[str] = []

    summary = await run_demo(
        output=messages.append,
        output_dir=tmp_path,
        reset_output=True,
    )

    assert_successful_summary(summary)
    assert_demo_server_is_closed(summary["base_url"])
    assert "Итоговая статистика Day 6:" in messages


@pytest.mark.socket
async def test_demo_reset_keeps_second_run_at_three_records(
    tmp_path: Path,
) -> None:
    first = await run_demo(
        output=lambda _: None,
        output_dir=tmp_path,
        reset_output=True,
    )
    second = await run_demo(
        output=lambda _: None,
        output_dir=tmp_path,
        reset_output=True,
    )

    assert_successful_summary(first)
    assert_successful_summary(second)
    assert first["stored_counts"] == second["stored_counts"] == EXPECTED_COUNTS
    assert_demo_server_is_closed(first["base_url"])
    assert_demo_server_is_closed(second["base_url"])
