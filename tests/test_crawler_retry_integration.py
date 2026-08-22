from unittest.mock import AsyncMock, call

import pytest

from src.crawler import AsyncCrawler
from src.errors import ParseError
from src.fetch_result import FetchOutcome, FetchResult


URL = "https://example.com/unstable"


def make_crawler(
    *,
    max_attempts: int | None = None,
) -> tuple[AsyncCrawler, AsyncMock, AsyncMock]:
    crawler = (
        AsyncCrawler()
        if max_attempts is None
        else AsyncCrawler(max_attempts=max_attempts)
    )
    fetcher = AsyncMock()
    sleep = AsyncMock()
    crawler.request_executor._fetcher = fetcher
    crawler.retry_strategy._sleep = sleep
    return crawler, fetcher, sleep


def test_default_max_attempts_matches_retry_strategy() -> None:
    crawler, _, _ = make_crawler()

    assert crawler.retry_strategy.max_retries == 3
    assert crawler.request_executor.retry_strategy is crawler.retry_strategy


@pytest.mark.asyncio
async def test_timeout_is_retried_and_final_error_is_saved() -> None:
    crawler, fetcher, sleep = make_crawler()
    fetcher.return_value = FetchResult.timeout(URL, error="Read timeout")

    result = await crawler.fetch_result(URL)

    assert result.outcome is FetchOutcome.TIMEOUT
    assert result.attempts == 4
    assert fetcher.await_count == 4
    assert sleep.await_args_list == [call(0.5), call(1.0), call(2.0)]
    assert crawler.final_errors[URL] == {
        "url": URL,
        "error_type": "TransientError",
        "outcome": "timeout",
        "status_code": None,
        "message": "Read timeout",
        "attempts": 4,
        "elapsed_seconds": pytest.approx(result.elapsed_seconds),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_retryable_http_status_exhausts_attempt_limit(status: int) -> None:
    crawler, fetcher, sleep = make_crawler()
    fetcher.return_value = FetchResult.http_error(URL, status)

    result = await crawler.fetch_result(URL)

    assert result.outcome is FetchOutcome.HTTP_ERROR
    assert result.status_code == status
    assert result.attempts == 4
    assert fetcher.await_count == 4
    assert sleep.await_args_list == [call(0.5), call(1.0), call(2.0)]
    assert crawler.final_errors[URL]["error_type"] == "TransientError"
    assert crawler.final_errors[URL]["attempts"] == 4


@pytest.mark.asyncio
async def test_network_error_exhausts_default_attempt_limit() -> None:
    crawler, fetcher, sleep = make_crawler()
    fetcher.return_value = FetchResult.network_error(URL, "Connection failed")

    result = await crawler.fetch_result(URL)

    assert result.outcome is FetchOutcome.NETWORK_ERROR
    assert result.attempts == 4
    assert fetcher.await_count == 4
    assert sleep.await_args_list == [call(0.5), call(1.0), call(2.0)]
    stats = crawler.get_error_stats()
    assert stats["total_attempts"] == 4
    assert stats["errors_by_type"] == {"NetworkError": 4}
    assert stats["scheduled_retries"] == 3
    assert stats["exhausted_retries"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404])
async def test_permanent_http_status_is_not_retried(status: int) -> None:
    crawler, fetcher, sleep = make_crawler()
    fetcher.return_value = FetchResult.http_error(URL, status)

    result = await crawler.fetch_result(URL)

    assert result.outcome is FetchOutcome.HTTP_ERROR
    assert result.status_code == status
    assert result.attempts == 1
    fetcher.assert_awaited_once_with(URL, attempt=1)
    sleep.assert_not_awaited()
    assert crawler.final_errors[URL]["error_type"] == "PermanentError"
    assert crawler.final_errors[URL]["attempts"] == 1
    stats = crawler.get_request_stats()
    assert stats["errors_by_type"] == {"PermanentError": 1}
    assert stats["permanent_error_urls"] == [URL]


@pytest.mark.asyncio
async def test_successful_retry_does_not_leave_a_final_error() -> None:
    crawler, fetcher, sleep = make_crawler()
    fetcher.side_effect = [
        FetchResult.http_error(URL, 503),
        FetchResult.success(URL, "recovered"),
    ]

    result = await crawler.fetch_result(URL)

    assert result.is_success
    assert result.attempts == 2
    assert fetcher.await_count == 2
    sleep.assert_awaited_once_with(0.5)
    assert URL not in crawler.final_errors
    stats = crawler.get_request_stats()
    assert stats["errors_by_type"] == {"TransientError": 1}
    assert stats["successful_retries"] == 1
    assert stats["average_retry_wait"] == 0.5


@pytest.mark.asyncio
async def test_explicit_single_attempt_disables_retry() -> None:
    crawler, fetcher, sleep = make_crawler(max_attempts=1)
    fetcher.return_value = FetchResult.http_error(URL, 503)

    result = await crawler.fetch_result(URL)

    assert result.outcome is FetchOutcome.HTTP_ERROR
    assert result.attempts == 1
    fetcher.assert_awaited_once_with(URL, attempt=1)
    sleep.assert_not_awaited()
    stats = crawler.get_error_stats()
    assert stats["total_attempts"] == 1
    assert stats["scheduled_retries"] == 0
    assert stats["exhausted_retries"] == 1


@pytest.mark.asyncio
async def test_error_statistics_include_attempts_retries_and_final_errors() -> None:
    crawler, fetcher, _ = make_crawler(max_attempts=2)
    fetcher.return_value = FetchResult.http_error(URL, 503)

    await crawler.fetch_result(URL)
    stats = crawler.get_error_stats()

    assert stats["total_executions"] == 1
    assert stats["total_attempts"] == 2
    assert stats["errors_by_type"] == {"TransientError": 2}
    assert stats["scheduled_retries"] == 1
    assert stats["exhausted_retries"] == 1
    assert stats["final_errors_count"] == 1
    assert stats["final_errors"] == {URL: crawler.final_errors[URL]}


@pytest.mark.asyncio
async def test_error_statistics_return_detached_final_error_records() -> None:
    crawler, fetcher, _ = make_crawler(max_attempts=1)
    fetcher.return_value = FetchResult.http_error(URL, 404)
    await crawler.fetch_result(URL)

    stats = crawler.get_error_stats()
    final_errors = stats["final_errors"]
    assert isinstance(final_errors, dict)
    record = final_errors[URL]
    assert isinstance(record, dict)
    record["message"] = "changed"

    assert crawler.final_errors[URL]["message"] == "HTTP 404"


@pytest.mark.asyncio
async def test_parse_error_is_recorded_but_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crawler, fetcher, sleep = make_crawler(max_attempts=3)
    fetcher.return_value = FetchResult.success(URL, "<html>broken</html>")

    def crash(html: str, url: str) -> dict[str, object]:
        raise ValueError("unexpected parser failure")

    monkeypatch.setattr(crawler._parser, "_parse_html", crash)

    with pytest.raises(ParseError, match="ValueError") as raised:
        await crawler.fetch_and_parse(URL)

    assert isinstance(raised.value.__cause__, ValueError)
    fetcher.assert_awaited_once_with(URL, attempt=1)
    sleep.assert_not_awaited()
    stats = crawler.get_request_stats()
    assert stats["errors_by_type"] == {"ParseError": 1}
    assert stats["scheduled_retries"] == 0
    assert stats["successful_retries"] == 0
    assert stats["average_retry_wait"] == 0.0
    assert stats["permanent_error_urls"] == [URL]
    assert crawler.final_errors[URL]["error_type"] == "ParseError"
