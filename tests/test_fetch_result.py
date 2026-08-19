from dataclasses import FrozenInstanceError

import pytest

from src.fetch_result import FetchOutcome, FetchResult


def test_success_result_has_typed_properties() -> None:
    result = FetchResult.success(
        "https://example.com",
        "<html>OK</html>",
        content_type="text/html",
        elapsed_seconds=0.25,
    )

    assert result.outcome is FetchOutcome.SUCCESS
    assert result.is_success is True
    assert result.is_retryable is False
    assert result.status_code == 200
    assert result.content == "<html>OK</html>"
    assert result.content_type == "text/html"
    assert result.error is None
    assert result.attempts == 1
    assert result.elapsed_seconds == 0.25


@pytest.mark.parametrize(
    ("result", "retryable"),
    [
        (FetchResult.http_error("https://example.com/404", 404), False),
        (FetchResult.http_error("https://example.com/429", 429), True),
        (FetchResult.http_error("https://example.com/503", 503), True),
        (FetchResult.timeout("https://example.com/slow"), True),
        (
            FetchResult.network_error(
                "https://example.com",
                "ClientConnectionError",
            ),
            True,
        ),
        (FetchResult.blocked("https://example.com/private"), False),
    ],
)
def test_failure_factories_and_retry_classification(
    result: FetchResult,
    retryable: bool,
) -> None:
    assert result.is_success is False
    assert result.is_retryable is retryable
    assert result.error


def test_result_is_immutable() -> None:
    result = FetchResult.success("https://example.com", "OK")

    with pytest.raises(FrozenInstanceError):
        result.content = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"url": "", "outcome": FetchOutcome.SUCCESS, "content": "OK", "status_code": 200},
            "url",
        ),
        (
            {"url": "https://example.com", "outcome": FetchOutcome.SUCCESS, "status_code": 200},
            "content",
        ),
        (
            {
                "url": "https://example.com",
                "outcome": FetchOutcome.SUCCESS,
                "content": "OK",
                "status_code": 200,
                "error": "contradiction",
            },
            "cannot contain",
        ),
        (
            {
                "url": "https://example.com",
                "outcome": FetchOutcome.HTTP_ERROR,
                "status_code": 404,
            },
            "error",
        ),
        (
            {
                "url": "https://example.com",
                "outcome": FetchOutcome.TIMEOUT,
                "status_code": 504,
                "error": "timeout",
            },
            "status code",
        ),
        (
            {
                "url": "https://example.com",
                "outcome": FetchOutcome.TIMEOUT,
                "error": "timeout",
                "attempts": 0,
            },
            "attempts",
        ),
        (
            {
                "url": "https://example.com",
                "outcome": FetchOutcome.TIMEOUT,
                "error": "timeout",
                "elapsed_seconds": float("nan"),
            },
            "elapsed_seconds",
        ),
        (
            {
                "url": "https://example.com",
                "outcome": FetchOutcome.SUCCESS,
                "content": "OK",
                "status_code": 200,
                "content_type": "",
            },
            "content_type",
        ),
        (
            {
                "url": "https://example.com",
                "outcome": FetchOutcome.TIMEOUT,
                "error": "timeout",
                "content_type": "text/html",
            },
            "content_type",
        ),
    ],
)
def test_inconsistent_results_are_rejected(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        FetchResult(**kwargs)
