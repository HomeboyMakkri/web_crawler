from unittest.mock import AsyncMock, call, patch

import pytest

from src.fetch_result import FetchOutcome, FetchResult
from src.politeness_manager import PolitenessManager


@pytest.mark.asyncio
async def test_disabled_policy_allows_request_without_fetching_robots() -> None:
    fetcher = AsyncMock()
    manager = PolitenessManager(fetcher=fetcher)

    result = await manager.prepare_request("https://example.com/page")

    assert result is None
    assert manager.rate_limiter is None
    assert manager.robots_parser is None
    fetcher.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limit_uses_domain_and_configured_minimum_delay() -> None:
    manager = PolitenessManager(
        fetcher=AsyncMock(),
        requests_per_second=2.0,
        min_delay=0.75,
    )
    assert manager.rate_limiter is not None
    acquire = AsyncMock()

    with patch.object(manager.rate_limiter, "acquire", acquire):
        result = await manager.prepare_request("https://Docs.Example.com/page")

    assert result is None
    acquire.assert_awaited_once_with(
        "docs.example.com",
        min_interval=0.75,
    )


@pytest.mark.asyncio
async def test_jitter_adds_random_fraction_to_required_interval() -> None:
    manager = PolitenessManager(
        fetcher=AsyncMock(),
        min_delay=0.5,
        jitter=0.4,
        random_source=lambda: 0.25,
    )
    assert manager.rate_limiter is not None
    acquire = AsyncMock()

    with patch.object(manager.rate_limiter, "acquire", acquire):
        result = await manager.prepare_request("https://example.com/page")

    assert result is None
    acquire.assert_awaited_once_with("example.com", min_interval=0.6)


@pytest.mark.asyncio
async def test_jitter_alone_enables_rate_limiting() -> None:
    manager = PolitenessManager(
        fetcher=AsyncMock(),
        jitter=0.2,
        random_source=lambda: 1.0,
    )
    assert manager.rate_limiter is not None
    acquire = AsyncMock()

    with patch.object(manager.rate_limiter, "acquire", acquire):
        await manager.prepare_request("https://example.com/page")

    acquire.assert_awaited_once_with("example.com", min_interval=0.2)


@pytest.mark.asyncio
async def test_zero_jitter_does_not_call_random_source() -> None:
    random_source = AsyncMock()
    manager = PolitenessManager(
        fetcher=AsyncMock(),
        min_delay=0.5,
        jitter=0.0,
        random_source=random_source,
    )
    assert manager.rate_limiter is not None

    with patch.object(manager.rate_limiter, "acquire", AsyncMock()):
        await manager.prepare_request("https://example.com/page")

    random_source.assert_not_called()


@pytest.mark.asyncio
async def test_allowed_url_uses_larger_robots_crawl_delay() -> None:
    robots = "User-agent: MyBot\nDisallow: /private\nCrawl-delay: 2"
    fetcher = AsyncMock(
        side_effect=lambda url: FetchResult.success(url, robots),
    )
    manager = PolitenessManager(
        fetcher=fetcher,
        requests_per_second=4.0,
        respect_robots=True,
        min_delay=0.5,
        user_agent="MyBot",
    )
    assert manager.rate_limiter is not None
    acquire = AsyncMock()

    with patch.object(manager.rate_limiter, "acquire", acquire):
        result = await manager.prepare_request("https://example.com/catalog")

    assert result is None
    fetcher.assert_awaited_once_with("https://example.com/robots.txt")
    assert acquire.await_args_list == [
        call("example.com", min_interval=0.5),
        call("example.com", min_interval=2.0),
    ]


@pytest.mark.asyncio
async def test_jitter_is_added_without_weakening_robots_crawl_delay() -> None:
    random_values = iter([0.0, 0.5])
    fetcher = AsyncMock(
        side_effect=lambda url: FetchResult.success(
            url,
            "User-agent: *\nCrawl-delay: 2",
        ),
    )
    manager = PolitenessManager(
        fetcher=fetcher,
        respect_robots=True,
        min_delay=0.5,
        jitter=0.4,
        random_source=lambda: next(random_values),
    )
    assert manager.rate_limiter is not None
    acquire = AsyncMock()

    with patch.object(manager.rate_limiter, "acquire", acquire):
        await manager.prepare_request("https://example.com/page")

    assert acquire.await_args_list == [
        call("example.com", min_interval=0.5),
        call("example.com", min_interval=2.2),
    ]


@pytest.mark.asyncio
async def test_disallowed_url_returns_blocked_result_without_page_fetch() -> None:
    robots = "User-agent: MyBot\nDisallow: /private/"
    fetcher = AsyncMock(
        side_effect=lambda url: FetchResult.success(url, robots),
    )
    manager = PolitenessManager(
        fetcher=fetcher,
        respect_robots=True,
        user_agent="MyBot",
    )
    assert manager.rate_limiter is not None
    acquire = AsyncMock()
    url = "https://example.com/private/page"

    with patch.object(manager.rate_limiter, "acquire", acquire):
        result = await manager.prepare_request(url)

    assert result is not None
    assert result.outcome is FetchOutcome.ROBOTS_BLOCKED
    assert result.url == url
    fetcher.assert_awaited_once_with("https://example.com/robots.txt")
    acquire.assert_awaited_once_with("example.com", min_interval=0.0)


@pytest.mark.asyncio
async def test_robots_rules_are_reused_for_same_origin() -> None:
    fetcher = AsyncMock(
        side_effect=lambda url: FetchResult.success(
            url,
            "User-agent: *\nAllow: /",
        ),
    )
    manager = PolitenessManager(fetcher=fetcher, respect_robots=True)
    assert manager.rate_limiter is not None

    with patch.object(manager.rate_limiter, "acquire", AsyncMock()):
        await manager.prepare_request("https://example.com/one")
        await manager.prepare_request("https://example.com/two")

    fetcher.assert_awaited_once_with("https://example.com/robots.txt")
    assert manager.robots_parser is not None
    assert manager.robots_parser.get_stats()["cache_hits"] == 1


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"fetcher": None}, "fetcher must be callable"),
        (
            {"fetcher": AsyncMock(), "respect_robots": 1},
            "respect_robots must be a boolean",
        ),
        (
            {"fetcher": AsyncMock(), "min_delay": -0.1},
            "min_delay must be a non-negative finite number",
        ),
        (
            {"fetcher": AsyncMock(), "min_delay": float("nan")},
            "min_delay must be a non-negative finite number",
        ),
        (
            {"fetcher": AsyncMock(), "jitter": -0.1},
            "jitter must be a non-negative finite number",
        ),
        (
            {"fetcher": AsyncMock(), "jitter": float("inf")},
            "jitter must be a non-negative finite number",
        ),
        (
            {"fetcher": AsyncMock(), "user_agent": "  "},
            "user_agent must be a non-empty string",
        ),
        (
            {"fetcher": AsyncMock(), "requests_per_second": 0},
            "requests_per_second must be a positive finite number",
        ),
        (
            {"fetcher": AsyncMock(), "random_source": None},
            "random_source must be callable",
        ),
    ],
)
def test_invalid_configuration_is_rejected(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        PolitenessManager(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("random_value", [-0.1, 1.1, float("nan"), True])
async def test_invalid_random_source_result_is_rejected(
    random_value: object,
) -> None:
    manager = PolitenessManager(
        fetcher=AsyncMock(),
        jitter=0.5,
        random_source=lambda: random_value,  # type: ignore[return-value]
    )

    with pytest.raises(ValueError, match="finite number from 0 to 1"):
        await manager.prepare_request("https://example.com/page")
