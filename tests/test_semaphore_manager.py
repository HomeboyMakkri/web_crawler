import asyncio

import pytest

from src.semaphore_manager import SemaphoreManager


async def wait_until(predicate, *, timeout: float = 0.2) -> None:
    """Yield to scheduled tasks until a deterministic state is reached."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_global_limit_caps_requests_across_domains() -> None:
    manager = SemaphoreManager(global_limit=2, per_domain_limit=2)
    release = asyncio.Event()
    peak_active = 0

    async def request(url: str) -> None:
        nonlocal peak_active
        async with manager.request_slot(url):
            peak_active = max(peak_active, manager.active_total)
            await release.wait()

    tasks = [
        asyncio.create_task(request("https://one.example/a")),
        asyncio.create_task(request("https://two.example/b")),
        asyncio.create_task(request("https://three.example/c")),
    ]
    await wait_until(lambda: manager.active_total == 2)

    assert peak_active == 2
    assert manager.get_stats()["waiting_global"] == 1

    release.set()
    await asyncio.gather(*tasks)
    assert manager.active_total == 0


@pytest.mark.asyncio
async def test_per_domain_limit_caps_same_domain_requests() -> None:
    manager = SemaphoreManager(global_limit=5, per_domain_limit=2)
    release = asyncio.Event()
    peak_for_domain = 0

    async def request(path: str) -> None:
        nonlocal peak_for_domain
        async with manager.request_slot(f"https://example.com/{path}"):
            peak_for_domain = max(
                peak_for_domain,
                manager.active_by_domain["example.com"],
            )
            await release.wait()

    tasks = [asyncio.create_task(request(str(index))) for index in range(3)]
    await wait_until(lambda: manager.active_total == 2)

    assert peak_for_domain == 2
    assert manager.get_stats()["waiting_by_domain"] == {"example.com": 1}

    release.set()
    await asyncio.gather(*tasks)
    assert manager.active_by_domain == {}


@pytest.mark.asyncio
async def test_different_domains_do_not_block_each_other() -> None:
    manager = SemaphoreManager(global_limit=3, per_domain_limit=1)
    release = asyncio.Event()

    async def request(url: str) -> None:
        async with manager.request_slot(url):
            await release.wait()

    tasks = [
        asyncio.create_task(request("https://one.example/page")),
        asyncio.create_task(request("https://two.example/page")),
        asyncio.create_task(request("https://three.example/page")),
    ]
    await wait_until(lambda: manager.active_total == 3)

    assert manager.active_by_domain == {
        "one.example": 1,
        "two.example": 1,
        "three.example": 1,
    }

    release.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_request_slot_releases_capacity_after_exception() -> None:
    manager = SemaphoreManager(global_limit=1, per_domain_limit=1)

    with pytest.raises(RuntimeError, match="request failed"):
        async with manager.request_slot("https://example.com/failing"):
            assert manager.active_total == 1
            raise RuntimeError("request failed")

    assert manager.active_total == 0
    assert manager.active_by_domain == {}

    async with asyncio.timeout(0.1):
        async with manager.request_slot("https://example.com/next"):
            assert manager.active_total == 1


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_leak_domain_slot() -> None:
    manager = SemaphoreManager(global_limit=1, per_domain_limit=2)
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def first_request() -> None:
        async with manager.request_slot("https://first.example/page"):
            first_entered.set()
            await release_first.wait()

    first = asyncio.create_task(first_request())
    await first_entered.wait()

    waiting = asyncio.create_task(
        manager.request_slot("https://second.example/page").__aenter__()
    )
    await wait_until(lambda: manager.get_stats()["waiting_global"] == 1)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    release_first.set()
    await first

    async with asyncio.timeout(0.1):
        async with manager.request_slot("https://second.example/next"):
            assert manager.active_by_domain == {"second.example": 1}


def test_domain_normalization() -> None:
    assert (
        SemaphoreManager.get_domain("HTTPS://Example.COM:8443/path")
        == "example.com"
    )


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("", "HTTP"),
        ("example.com/page", "HTTP"),
        ("mailto:test@example.com", "HTTP"),
        ("https:///missing-host", "HTTP"),
    ],
)
def test_invalid_url_is_rejected(url: str, message: str) -> None:
    manager = SemaphoreManager()

    with pytest.raises(ValueError, match=message):
        manager.get_domain(url)


@pytest.mark.parametrize(
    ("kwargs", "parameter_name"),
    [
        ({"global_limit": 0}, "global_limit"),
        ({"global_limit": True}, "global_limit"),
        ({"per_domain_limit": -1}, "per_domain_limit"),
        ({"per_domain_limit": 1.5}, "per_domain_limit"),
    ],
)
def test_invalid_limits_are_rejected(kwargs: dict, parameter_name: str) -> None:
    with pytest.raises(ValueError, match=parameter_name):
        SemaphoreManager(**kwargs)
