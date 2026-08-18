from unittest.mock import patch

from src.crawler import AsyncCrawler


def test_crawler_and_executor_share_one_retry_strategy() -> None:
    crawler = AsyncCrawler(max_attempts=3)

    assert crawler.request_executor.retry_strategy is crawler.retry_strategy
    assert crawler.retry_strategy.max_retries == 2


def test_crawler_aggregates_request_statistics_from_components() -> None:
    crawler = AsyncCrawler()
    transport_stats = {
        "total_requests": 8,
        "successful_requests": 5,
        "failed_requests": 3,
        "http_errors": 1,
        "network_errors": 1,
        "timeouts": 1,
        "current_requests_per_second": 2.0,
        "average_request_time": 0.25,
    }
    politeness_stats = {
        "min_delay": 0.5,
        "jitter": 0.2,
        "rate_limiting_enabled": True,
        "rate_limited_requests": 9,
        "delayed_requests": 6,
        "total_rate_limit_wait": 3.0,
        "average_rate_limit_wait": 1 / 3,
        "robots_enabled": True,
        "robots_network_fetches": 1,
        "robots_cache_hits": 4,
        "robots_allowed": 5,
        "robots_blocked": 1,
    }
    retry_stats = {
        "scheduled_retries": 2,
        "total_backoff_time": 1.5,
    }

    with (
        patch.object(crawler._transport, "get_stats", return_value=transport_stats),
        patch.object(
            crawler.politeness_manager,
            "get_stats",
            return_value=politeness_stats,
        ),
        patch.object(
            crawler.retry_strategy,
            "get_stats",
            return_value=retry_stats,
        ),
    ):
        stats = crawler.get_request_stats()

    assert stats == {
        "total_requests": 8,
        "successful_requests": 5,
        "failed_requests": 3,
        "http_errors": 1,
        "network_errors": 1,
        "timeouts": 1,
        "current_requests_per_second": 2.0,
        "average_request_time": 0.25,
        "rate_limited_requests": 9,
        "delayed_requests": 6,
        "total_rate_limit_wait": 3.0,
        "average_rate_limit_wait": 1 / 3,
        "scheduled_retries": 2,
        "total_backoff_time": 1.5,
        "robots_network_fetches": 1,
        "robots_cache_hits": 4,
        "robots_allowed": 5,
        "robots_blocked": 1,
    }


def test_crawler_request_stats_are_zero_before_requests() -> None:
    crawler = AsyncCrawler()

    assert crawler.get_request_stats() == {
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
        "robots_network_fetches": 0,
        "robots_cache_hits": 0,
        "robots_allowed": 0,
        "robots_blocked": 0,
    }
