import asyncio

import pytest

from src.crawl_reporter import CrawlReporter


def stats() -> dict[str, int | float]:
    return {
        "pages_scheduled": 5,
        "pages_queued": 1,
        "pages_active": 1,
        "pages_successful": 2,
        "pages_failed": 1,
        "pages_completed": 3,
        "active_requests": 1,
        "max_depth_reached": 1,
        "total_text_length": 100,
        "total_links": 4,
        "total_images": 2,
        "elapsed_seconds": 1.5,
        "pages_per_second": 2.0,
    }


def test_report_once_formats_a_readable_snapshot() -> None:
    messages: list[str] = []
    reporter = CrawlReporter(stats, output=messages.append)

    reporter.report_once(final=True)

    assert messages == [
        "Итог: обработано 3/5 | успешно 2 | в очереди 1 | "
        "активно 1 | ошибок 1 | скорость 2.00 стр/с"
    ]


@pytest.mark.asyncio
async def test_run_reports_repeatedly_until_cancelled() -> None:
    reported_twice = asyncio.Event()
    messages: list[str] = []

    def collect(message: str) -> None:
        messages.append(message)
        if len(messages) >= 2:
            reported_twice.set()

    reporter = CrawlReporter(stats, interval=0.001, output=collect)
    task = asyncio.create_task(reporter.run())

    await asyncio.wait_for(reported_twice.wait(), timeout=0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(messages) >= 2
    assert all(message.startswith("Прогресс:") for message in messages)


@pytest.mark.parametrize("interval", [0, -1, True])
def test_reporter_validates_interval(interval: float) -> None:
    with pytest.raises(ValueError, match="interval"):
        CrawlReporter(stats, interval=interval)
