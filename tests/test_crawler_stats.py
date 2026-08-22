from copy import deepcopy
from typing import cast

import pytest

from src.crawler_stats import CrawlerStats, RequestStatsSnapshot, RunStats
from src.fetch_result import FetchResult


def crawl_stats(**overrides: int | float) -> dict[str, int | float]:
    snapshot: dict[str, int | float] = {
        "pages_scheduled": 0,
        "pages_queued": 0,
        "pages_active": 0,
        "pages_successful": 0,
        "pages_failed": 0,
        "pages_blocked": 0,
        "pages_completed": 0,
        "active_requests": 0,
        "max_depth_reached": 0,
        "total_text_length": 0,
        "total_links": 0,
        "total_images": 0,
        "elapsed_seconds": 0.0,
        "pages_per_second": 0.0,
    }
    snapshot.update(overrides)
    return snapshot


def request_stats(**overrides: object) -> RequestStatsSnapshot:
    snapshot: RequestStatsSnapshot = {
        "total_requests": 0,
        "successful_requests": 0,
        "failed_requests": 0,
        "http_errors": 0,
        "network_errors": 0,
        "timeouts": 0,
        "current_requests_per_second": 0.0,
        "average_request_time": 0.0,
        "rate_limited_requests": 0,
        "delayed_requests": 0,
        "total_rate_limit_wait": 0.0,
        "average_rate_limit_wait": 0.0,
        "scheduled_retries": 0,
        "total_backoff_time": 0.0,
        "errors_by_type": {},
        "successful_retries": 0,
        "average_retry_wait": 0.0,
        "permanent_error_urls": [],
        "robots_network_fetches": 0,
        "robots_cache_hits": 0,
        "robots_allowed": 0,
        "robots_blocked": 0,
    }
    return cast(RequestStatsSnapshot, {**snapshot, **overrides})


def build_zero_snapshot(stats: CrawlerStats) -> RunStats:
    return stats.build_snapshot(
        crawl_stats=crawl_stats(),
        elapsed_seconds=0.0,
        page_outcomes={},
        request_stats=request_stats(),
        request_stats_baseline=None,
        final_errors={},
        storage_stats=None,
        storage_stats_baseline=None,
    )


def test_build_snapshot_is_stateless_and_detached() -> None:
    stats = CrawlerStats()
    first = build_zero_snapshot(stats)

    assert first["total_pages"] == 0
    assert first["pages_per_second"] == 0.0
    assert first["status_codes"] == {}
    assert first["top_domains"] == []
    assert first["storage_stats"] is None

    status_codes = first["status_codes"]
    request_snapshot = first["request_stats"]
    assert isinstance(status_codes, dict)
    assert isinstance(request_snapshot, dict)
    status_codes["999"] = 1
    request_snapshot["total_requests"] = 99

    second = build_zero_snapshot(stats)
    assert second["status_codes"] == {}
    assert second["request_stats"] == request_stats()


def test_build_snapshot_derives_page_rate_statuses_and_top_domains() -> None:
    stats = CrawlerStats()
    outcomes = {
        "https://Alpha.Example:8443/one": FetchResult.success(
            "https://Alpha.Example:8443/one",
            "html",
            status_code=201,
        ),
        "https://alpha.example/two": FetchResult.http_error(
            "https://alpha.example/two",
            404,
        ),
        "https://beta.example/page": None,
        "https://[invalid/page": FetchResult.timeout("https://[invalid/page"),
    }

    snapshot = stats.build_snapshot(
        crawl_stats=crawl_stats(
            pages_scheduled=4,
            pages_successful=1,
            pages_failed=2,
            pages_blocked=1,
            pages_completed=4,
            total_text_length=12,
            total_links=3,
            total_images=2,
        ),
        elapsed_seconds=2.0,
        page_outcomes=outcomes,
        request_stats=request_stats(),
        request_stats_baseline=None,
        final_errors={},
        storage_stats=None,
        storage_stats_baseline=None,
    )

    assert snapshot["total_pages"] == 4
    assert snapshot["pages_per_second"] == 2.0
    assert snapshot["status_codes"] == {"201": 1, "404": 1}
    assert snapshot["top_domains"] == [
        {"domain": "alpha.example", "pages": 2},
        {"domain": "beta.example", "pages": 1},
    ]


def test_request_delta_is_per_run_deterministic_and_does_not_mutate_inputs() -> None:
    stats = CrawlerStats()
    baseline = request_stats(
        total_requests=5,
        successful_requests=3,
        failed_requests=1,
        average_request_time=0.25,
        scheduled_retries=1,
        total_backoff_time=0.5,
        errors_by_type={"TransientError": 1},
        permanent_error_urls=["https://old.example"],
    )
    current = request_stats(
        total_requests=9,
        successful_requests=5,
        failed_requests=2,
        http_errors=1,
        current_requests_per_second=8.5,
        average_request_time=0.5,
        scheduled_retries=3,
        total_backoff_time=2.5,
        errors_by_type={"TransientError": 3, "PermanentError": 1},
        successful_retries=1,
        permanent_error_urls=["https://old.example", "https://new.example"],
    )
    final_errors = {
        "https://repeated.example": {"error_type": "PermanentError"},
        "https://ignored.example": {"error_type": "TransientError"},
    }
    original_baseline = deepcopy(baseline)
    original_current = deepcopy(current)
    original_final_errors = deepcopy(final_errors)

    delta = stats.request_stats_delta(
        current,
        baseline,
        final_errors=final_errors,
    )

    assert delta["total_requests"] == 4
    assert delta["successful_requests"] == 2
    assert delta["failed_requests"] == 1
    assert delta["http_errors"] == 1
    assert delta["current_requests_per_second"] == 8.5
    assert delta["average_request_time"] == pytest.approx(5 / 6)
    assert delta["scheduled_retries"] == 2
    assert delta["average_retry_wait"] == 1.0
    assert delta["errors_by_type"] == {
        "PermanentError": 1,
        "TransientError": 2,
    }
    assert delta["permanent_error_urls"] == [
        "https://new.example",
        "https://repeated.example",
    ]
    assert baseline == original_baseline
    assert current == original_current
    assert final_errors == original_final_errors


def test_missing_request_baseline_produces_detached_zero_delta() -> None:
    stats = CrawlerStats()
    current = request_stats(
        total_requests=7,
        successful_requests=5,
        current_requests_per_second=4.0,
        errors_by_type={"TransientError": 2},
        permanent_error_urls=["https://old.example"],
    )

    delta = stats.request_stats_delta(current, None, final_errors={})

    assert delta == request_stats()
    errors = delta["errors_by_type"]
    permanent_urls = delta["permanent_error_urls"]
    assert isinstance(errors, dict)
    assert isinstance(permanent_urls, list)
    errors["changed"] = 1
    permanent_urls.append("https://changed.example")
    assert current["errors_by_type"] == {"TransientError": 2}
    assert current["permanent_error_urls"] == ["https://old.example"]


def test_storage_delta_supports_single_and_composite_snapshots() -> None:
    stats = CrawlerStats()
    baseline = {
        "json": {
            "saved_records": 2,
            "failed_saves": 1,
            "retried_saves": 0,
        },
        "sqlite": {
            "saved_records": 4,
            "failed_saves": 0,
            "retried_saves": 1,
        },
    }
    current = {
        "json": {
            "saved_records": 5,
            "failed_saves": 1,
            "retried_saves": 1,
        },
        "sqlite": {
            "saved_records": 6,
            "failed_saves": 1,
            "retried_saves": 1,
        },
    }

    assert stats.storage_stats_delta(current, baseline) == {
        "json": {
            "saved_records": 3,
            "failed_saves": 0,
            "retried_saves": 1,
        },
        "sqlite": {
            "saved_records": 2,
            "failed_saves": 1,
            "retried_saves": 0,
        },
    }
    assert stats.storage_stats_delta(
        {"saved_records": 3, "failed_saves": 1},
        {"saved_records": 1, "failed_saves": 0},
    ) == {"saved_records": 2, "failed_saves": 1}
    assert stats.storage_stats_delta(None, None) is None


@pytest.mark.parametrize("invalid_value", [True, 1.5, "1", None])
def test_storage_delta_rejects_non_integer_counters(invalid_value: object) -> None:
    with pytest.raises(
        RuntimeError,
        match="storage statistics must contain integers",
    ):
        CrawlerStats.storage_stats_delta(
            {"saved_records": invalid_value},
            {"saved_records": 0},
        )
