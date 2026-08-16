import pytest

from src.day4_demo import create_demo_app, run_demo


@pytest.mark.asyncio
async def test_demo_exercises_complete_day4_pipeline() -> None:
    messages: list[str] = []

    summary = await run_demo(
        output=messages.append,
        robots_crawl_delay=None,
        requests_per_second=1_000_000_000.0,
        min_delay=0.0,
        jitter=0.0,
        retry_base_delay=0.001,
        progress_interval=0.001,
    )

    base_url = summary["base_url"]
    assert summary["processed_urls"] == [
        base_url,
        f"{base_url}/public",
        f"{base_url}/unstable",
    ]
    assert summary["blocked_urls"] == {
        f"{base_url}/private": "Blocked by robots.txt"
    }
    assert summary["failed_urls"] == {}
    assert summary["server_state"] == {
        "unstable_requests": 2,
        "private_requests": 0,
    }

    crawl_stats = summary["crawl_stats"]
    assert crawl_stats["pages_successful"] == 3
    assert crawl_stats["pages_blocked"] == 1
    assert crawl_stats["pages_failed"] == 0

    request_stats = summary["request_stats"]
    assert request_stats["total_requests"] == 5
    assert request_stats["successful_requests"] == 4
    assert request_stats["http_errors"] == 1
    assert request_stats["scheduled_retries"] == 1
    assert request_stats["robots_network_fetches"] == 1
    assert request_stats["robots_cache_hits"] == 4
    assert request_stats["robots_blocked"] == 1

    assert any(message.startswith("Прогресс:") for message in messages)
    assert any(message.startswith("Итог:") for message in messages)
    assert any("HTTP-запросов" in message for message in messages)
    assert "Итоговая статистика Day 4:" in messages


@pytest.mark.parametrize(
    "crawl_delay",
    [-1, 0.5, True],
)
def test_demo_app_validates_robots_crawl_delay(crawl_delay: object) -> None:
    with pytest.raises(ValueError, match="robots_crawl_delay"):
        create_demo_app(
            robots_crawl_delay=crawl_delay,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_demo_validates_output() -> None:
    with pytest.raises(ValueError, match="output"):
        await run_demo(output="stdout")  # type: ignore[arg-type]
