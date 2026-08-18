from collections.abc import Callable

import pytest

from src.error_tracker import ErrorTracker
from src.errors import ParseError
from src.fetch_result import FetchResult


URL = "https://example.com/page"


def retry_stats(**overrides: object) -> dict[str, object]:
    stats: dict[str, object] = {
        "errors_by_type": {},
        "scheduled_retries": 0,
        "successful_retries": 0,
        "average_retry_wait": 0.0,
        "permanent_error_urls": [],
    }
    stats.update(overrides)
    return stats


def record_invalid_fetch_result(tracker: ErrorTracker) -> None:
    tracker.record_fetch_result("failed")  # type: ignore[arg-type]


def record_invalid_parse_error(tracker: ErrorTracker) -> None:
    tracker.record_parse_error(ValueError("bad"))  # type: ignore[arg-type]


def pass_invalid_retry_stats(tracker: ErrorTracker) -> None:
    tracker.get_stats([])  # type: ignore[arg-type]


def test_fetch_failure_is_converted_to_structured_record() -> None:
    tracker = ErrorTracker()
    result = FetchResult.http_error(
        URL,
        503,
        attempts=3,
        elapsed_seconds=1.25,
    )

    tracker.record_fetch_result(result)

    assert tracker.final_errors[URL] == {
        "url": URL,
        "error_type": "TransientError",
        "outcome": "http_error",
        "status_code": 503,
        "message": "HTTP 503",
        "attempts": 3,
        "elapsed_seconds": 1.25,
    }


def test_success_clears_previous_error_and_robots_block_is_not_an_error() -> None:
    tracker = ErrorTracker()
    tracker.record_fetch_result(FetchResult.timeout(URL))

    tracker.record_fetch_result(FetchResult.success(URL, "recovered"))
    tracker.record_fetch_result(FetchResult.blocked(f"{URL}/private"))

    assert tracker.final_errors == {}


def test_parse_error_is_merged_with_retry_statistics() -> None:
    tracker = ErrorTracker()
    tracker.record_parse_error(ParseError("parser crashed", url=URL))

    stats = tracker.get_stats(
        retry_stats(
            errors_by_type={"TransientError": 2},
            permanent_error_urls=["https://example.com/missing"],
        ),
    )

    assert stats["errors_by_type"] == {
        "TransientError": 2,
        "ParseError": 1,
    }
    assert stats["permanent_error_urls"] == [
        "https://example.com/missing",
        URL,
    ]
    assert stats["final_errors_count"] == 1
    assert stats["final_errors"] == {URL: tracker.final_errors[URL]}


def test_stats_snapshot_is_detached() -> None:
    tracker = ErrorTracker()
    tracker.record_fetch_result(FetchResult.http_error(URL, 404))

    stats = tracker.get_stats(retry_stats())
    final_errors = stats["final_errors"]
    assert isinstance(final_errors, dict)
    record = final_errors[URL]
    assert isinstance(record, dict)
    record["message"] = "changed"
    errors_by_type = stats["errors_by_type"]
    assert isinstance(errors_by_type, dict)
    errors_by_type["Changed"] = 1

    assert tracker.final_errors[URL]["message"] == "HTTP 404"
    assert tracker.get_stats(retry_stats())["errors_by_type"] == {}


def test_clear_final_errors_keeps_lifetime_parse_statistics() -> None:
    tracker = ErrorTracker()
    tracker.record_parse_error(ParseError("parser crashed", url=URL))

    tracker.clear_final_errors()
    stats = tracker.get_stats(retry_stats())

    assert tracker.final_errors == {}
    assert stats["final_errors_count"] == 0
    assert stats["errors_by_type"] == {"ParseError": 1}
    assert stats["permanent_error_urls"] == [URL]


@pytest.mark.parametrize(
    ("action", "message"),
    [
        (record_invalid_fetch_result, "FetchResult"),
        (record_invalid_parse_error, "ParseError"),
        (pass_invalid_retry_stats, "mapping"),
        (
            lambda tracker: tracker.get_stats(
                retry_stats(errors_by_type=[]),
            ),
            "errors_by_type",
        ),
        (
            lambda tracker: tracker.get_stats(
                retry_stats(permanent_error_urls="url"),
            ),
            "permanent_error_urls",
        ),
    ],
)
def test_invalid_inputs_are_rejected(
    action: Callable[[ErrorTracker], object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        action(ErrorTracker())
