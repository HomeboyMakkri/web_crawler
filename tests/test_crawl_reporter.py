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
        "pages_blocked": 1,
        "pages_completed": 3,
        "active_requests": 1,
        "max_depth_reached": 1,
        "total_text_length": 100,
        "total_links": 4,
        "total_images": 2,
        "elapsed_seconds": 1.5,
        "pages_per_second": 2.0,
    }


def request_stats() -> dict[str, object]:
    return {
        "total_requests": 8,
        "current_requests_per_second": 2.5,
        "average_rate_limit_wait": 0.375,
        "scheduled_retries": 2,
        "successful_retries": 1,
        "average_retry_wait": 0.75,
        "errors_by_type": {
            "TransientError": 3,
            "PermanentError": 1,
        },
        "permanent_error_urls": ["https://example.com/missing"],
        "robots_blocked": 1,
    }


def test_report_once_formats_a_readable_snapshot() -> None:
    messages: list[str] = []
    reporter = CrawlReporter(stats, output=messages.append)

    reporter.report_once(final=True)

    assert messages == [
        "Итог: обработано 3/5 | прогресс 100.0% | успешно 2 | "
        "в очереди 1 | активно страниц 1 | активно запросов 1 | "
        "ошибок 1 | заблокировано 1 | скорость 2.00 стр/с | ETA --"
    ]


def test_report_once_includes_request_and_politeness_statistics() -> None:
    messages: list[str] = []
    reporter = CrawlReporter(
        stats,
        request_stats_provider=request_stats,
        output=messages.append,
    )

    reporter.report_once()

    assert messages == [
        "Прогресс: обработано 3/5 | прогресс 60.0% | успешно 2 | "
        "в очереди 1 | активно страниц 1 | активно запросов 1 | "
        "ошибок 1 | заблокировано 1 | скорость 2.00 стр/с | ETA 1.0 с | "
        "HTTP-запросов 8 (2.50/с) | ср. задержка 0.375 с | retry 2 "
        "(успешно 1, ср. ожидание 0.750 с) | "
        "типы ошибок PermanentError=1, TransientError=3 | "
        "постоянных URL 1 | robots.txt блокировок 1"
    ]


def test_request_statistics_use_safe_defaults_for_missing_fields() -> None:
    message = CrawlReporter.format_stats(stats(), request_stats={})

    assert "HTTP-запросов 0 (0.00/с)" in message
    assert "ср. задержка 0.000 с" in message
    assert "retry 0 (успешно 0, ср. ожидание 0.000 с)" in message
    assert "типы ошибок нет" in message
    assert "постоянных URL 0" in message
    assert "robots.txt блокировок 0" in message


def test_zero_state_has_zero_percentage_and_no_eta() -> None:
    zero = stats() | {
        "pages_scheduled": 0,
        "pages_queued": 0,
        "pages_active": 0,
        "pages_completed": 0,
        "pages_per_second": 0.0,
        "active_requests": 0,
    }

    message = CrawlReporter.format_stats(zero)

    assert "прогресс 0.0%" in message
    assert "активно страниц 0" in message
    assert "активно запросов 0" in message
    assert "ETA --" in message


def test_zero_throughput_and_no_remaining_work_have_no_eta() -> None:
    stalled = stats() | {"pages_per_second": 0.0}
    finished = stats() | {"pages_queued": 0, "pages_active": 0}

    assert "ETA --" in CrawlReporter.format_stats(stalled)
    assert "ETA --" in CrawlReporter.format_stats(finished)


def test_growing_scheduled_work_can_reduce_percentage_and_change_eta() -> None:
    before = stats()
    after = stats() | {"pages_scheduled": 8, "pages_queued": 4}

    before_message = CrawlReporter.format_stats(before)
    after_message = CrawlReporter.format_stats(after)

    assert "прогресс 60.0%" in before_message
    assert "ETA 1.0 с" in before_message
    assert "прогресс 37.5%" in after_message
    assert "ETA 2.5 с" in after_message


def test_final_report_forces_complete_percentage_and_no_eta() -> None:
    message = CrawlReporter.format_stats(stats(), final=True)

    assert "прогресс 100.0%" in message
    assert "ETA --" in message


def test_missing_active_request_stat_uses_safe_zero() -> None:
    snapshot = stats()
    snapshot.pop("active_requests")

    message = CrawlReporter.format_stats(snapshot)

    assert "активно запросов 0" in message


def test_output_failure_is_logged_and_does_not_escape(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_output(message: str) -> None:
        raise OSError("terminal unavailable")

    reporter = CrawlReporter(stats, output=fail_output)

    with caplog.at_level("ERROR", logger="src.crawl_reporter"):
        reporter.report_once()

    assert "Could not report crawl progress" in caplog.text
    assert "terminal unavailable" in caplog.text


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


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"interval": 0}, "interval"),
        ({"interval": -1}, "interval"),
        ({"interval": True}, "interval"),
        ({"request_stats_provider": "stats"}, "request_stats_provider"),
    ],
)
def test_reporter_validates_configuration(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        CrawlReporter(stats, **kwargs)  # type: ignore[arg-type]
