import json

import pytest

from src.day5_demo import run_demo


@pytest.mark.socket
@pytest.mark.asyncio
async def test_demo_exercises_complete_day5_pipeline(tmp_path) -> None:
    messages: list[str] = []
    report_path = tmp_path / "day5_errors.json"

    summary = await run_demo(
        output=messages.append,
        report_path=report_path,
        slow_delay=0.2,
        total_timeout=0.02,
        retry_base_delay=0.001,
        progress_interval=0.001,
    )

    base_url = summary["base_url"]
    assert summary["processed_urls"] == [
        base_url,
        f"{base_url}/rate-limited",
    ]
    assert set(summary["failed_urls"]) == {
        f"{base_url}/missing",
        f"{base_url}/slow",
        f"{base_url}/unavailable",
    }
    assert summary["server_state"] == {
        "rate_limited_requests": 2,
        "unavailable_requests": 3,
        "missing_requests": 1,
        "timeout_requests": 3,
    }

    error_stats = summary["error_stats"]
    assert error_stats["errors_by_type"] == {
        "PermanentError": 1,
        "TransientError": 7,
    }
    assert error_stats["successful_retries"] == 1
    assert error_stats["scheduled_retries"] == 5
    assert error_stats["final_errors_count"] == 3
    assert set(error_stats["permanent_error_urls"]) == {
        f"{base_url}/missing",
    }

    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["errors"] == summary["final_errors"]
    assert saved["statistics"] == summary["error_stats"]
    assert any("типы ошибок" in message for message in messages)
    assert "Итоговая статистика Day 5:" in messages
