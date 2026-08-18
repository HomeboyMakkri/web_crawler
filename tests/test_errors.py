import pytest

from src.errors import (
    CrawlerError,
    NetworkError,
    ParseError,
    PermanentError,
    TransientError,
    classify_fetch_result,
)
from src.fetch_result import FetchResult


URL = "https://example.com/page"


def test_error_hierarchy_and_retryability_are_explicit() -> None:
    transient = TransientError("temporary", url=URL)
    network = NetworkError("connection lost", url=URL)
    permanent = PermanentError("not found", url=URL, status_code=404)
    parse = ParseError("invalid document", url=URL)

    assert isinstance(transient, CrawlerError)
    assert isinstance(network, CrawlerError)
    assert isinstance(permanent, CrawlerError)
    assert isinstance(parse, CrawlerError)
    assert transient.retryable is True
    assert network.retryable is True
    assert permanent.retryable is False
    assert parse.retryable is False
    assert str(parse) == "invalid document"


@pytest.mark.parametrize("status", range(400, 600))
def test_every_http_error_status_is_classified(status: int) -> None:
    result = FetchResult.http_error(
        f"{URL}/{status}",
        status,
        attempts=2,
    )

    error = classify_fetch_result(result)

    expected_type = (
        TransientError
        if status == 429 or 500 <= status <= 599
        else PermanentError
    )
    assert type(error) is expected_type
    assert error is not None
    assert error.url == result.url
    assert error.status_code == status
    assert error.fetch_result is result
    assert error.attempt == 2
    assert str(error) == f"HTTP {status}"


def test_timeout_is_classified_as_transient_error() -> None:
    result = FetchResult.timeout(
        URL,
        error="Read timeout",
        attempts=3,
    )

    error = classify_fetch_result(result)

    assert type(error) is TransientError
    assert error is not None
    assert error.status_code is None
    assert error.fetch_result is result
    assert error.attempt == 3
    assert str(error) == "Read timeout"


def test_network_failure_is_classified_as_network_error() -> None:
    result = FetchResult.network_error(
        URL,
        "ClientConnectorError: DNS lookup failed",
    )

    error = classify_fetch_result(result)

    assert type(error) is NetworkError
    assert error is not None
    assert error.retryable is True
    assert error.fetch_result is result


@pytest.mark.parametrize(
    "result",
    [
        FetchResult.success(URL, "OK"),
        FetchResult.success(URL, "redirected", status_code=302),
        FetchResult.blocked(f"{URL}/private"),
    ],
)
def test_non_error_outcomes_are_not_classified(result: FetchResult) -> None:
    assert classify_fetch_result(result) is None


def test_parse_error_can_carry_parser_context_without_fetch_result() -> None:
    error = ParseError(
        "lxml rejected malformed input",
        url=URL,
        attempt=1,
    )

    assert error.url == URL
    assert error.status_code is None
    assert error.fetch_result is None
    assert error.attempt == 1


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"message": "", "url": URL}, "message"),
        ({"message": "error", "url": ""}, "url"),
        ({"message": "error", "url": URL, "status_code": 99}, "status_code"),
        ({"message": "error", "url": URL, "status_code": True}, "status_code"),
        ({"message": "error", "url": URL, "attempt": 0}, "attempt"),
        ({"message": "error", "url": URL, "attempt": True}, "attempt"),
        ({"message": "error", "url": URL, "fetch_result": "error"}, "fetch_result"),
    ],
)
def test_crawler_error_rejects_invalid_context(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        CrawlerError(**kwargs)  # type: ignore[arg-type]


def test_crawler_error_rejects_context_that_disagrees_with_result() -> None:
    result = FetchResult.http_error(URL, 503, attempts=2)

    with pytest.raises(ValueError, match="URL"):
        TransientError("error", url="https://other.example", fetch_result=result)
    with pytest.raises(ValueError, match="status"):
        TransientError(
            "error",
            url=URL,
            status_code=500,
            fetch_result=result,
        )
    with pytest.raises(ValueError, match="attempts"):
        TransientError(
            "error",
            url=URL,
            fetch_result=result,
            attempt=1,
        )


def test_classifier_rejects_non_fetch_result() -> None:
    with pytest.raises(ValueError, match="FetchResult"):
        classify_fetch_result("HTTP 503")  # type: ignore[arg-type]
