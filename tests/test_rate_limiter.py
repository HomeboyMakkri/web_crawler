import asyncio
from collections.abc import Awaitable, Callable

import pytest

from src.rate_limiter import RateLimiter


class FakeTime:
    """Controllable monotonic clock that makes limiter tests instantaneous."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


def make_limiter(
    rate: float,
    *,
    per_domain: bool = True,
    fake_time: FakeTime | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> tuple[RateLimiter, FakeTime]:
    fake = fake_time or FakeTime()
    limiter = RateLimiter(
        rate,
        per_domain=per_domain,
        clock=fake.monotonic,
        sleep=sleep or fake.sleep,
    )
    return limiter, fake


@pytest.mark.asyncio
async def test_same_domain_requests_are_spaced_by_rate() -> None:
    limiter, fake = make_limiter(2.0)

    await limiter.acquire("Example.COM")
    await limiter.acquire("example.com")
    await limiter.acquire("example.com.")

    assert fake.sleeps == pytest.approx([0.5, 0.5])
    assert fake.now == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_different_domains_have_independent_schedules() -> None:
    limiter, fake = make_limiter(1.0)

    await limiter.acquire("one.example")
    await limiter.acquire("two.example")
    await limiter.acquire("one.example")

    assert fake.sleeps == pytest.approx([1.0])


@pytest.mark.asyncio
async def test_global_mode_shares_one_schedule_between_domains() -> None:
    limiter, fake = make_limiter(4.0, per_domain=False)

    await limiter.acquire("one.example")
    await limiter.acquire("two.example")
    await limiter.acquire()

    assert fake.sleeps == pytest.approx([0.25, 0.25])


@pytest.mark.asyncio
async def test_min_interval_can_apply_a_stricter_dynamic_delay() -> None:
    limiter, fake = make_limiter(4.0)

    await limiter.acquire("example.com")
    await limiter.acquire("example.com", min_interval=2.0)
    await limiter.acquire("example.com")

    assert fake.sleeps == pytest.approx([2.0, 0.25])


@pytest.mark.asyncio
async def test_concurrent_callers_reserve_distinct_slots() -> None:
    waits: list[float] = []

    async def record_sleep(delay: float) -> None:
        waits.append(delay)

    fake = FakeTime()
    limiter, _ = make_limiter(2.0, fake_time=fake, sleep=record_sleep)

    await asyncio.gather(
        limiter.acquire("example.com"),
        limiter.acquire("example.com"),
        limiter.acquire("example.com"),
    )

    assert sorted(waits) == pytest.approx([0.5, 1.0])


@pytest.mark.asyncio
async def test_sleeping_domain_does_not_hold_state_lock() -> None:
    sleep_started = asyncio.Event()
    release_sleep = asyncio.Event()

    async def controlled_sleep(delay: float) -> None:
        sleep_started.set()
        await release_sleep.wait()

    fake = FakeTime()
    limiter, _ = make_limiter(1.0, fake_time=fake, sleep=controlled_sleep)
    await limiter.acquire("slow.example")

    waiting = asyncio.create_task(limiter.acquire("slow.example"))
    await sleep_started.wait()

    async with asyncio.timeout(0.1):
        await limiter.acquire("other.example")

    release_sleep.set()
    await waiting


@pytest.mark.asyncio
async def test_stats_track_requests_and_waiting_time() -> None:
    limiter, _ = make_limiter(2.0)

    await limiter.acquire("one.example")
    await limiter.acquire("one.example")
    await limiter.acquire("two.example")

    assert limiter.get_stats() == {
        "requests_per_second": 2.0,
        "per_domain": True,
        "min_interval": 0.5,
        "total_requests": 3,
        "delayed_requests": 1,
        "total_wait_time": 0.5,
        "average_wait_time": pytest.approx(1 / 6),
        "requests_by_domain": {"one.example": 2, "two.example": 1},
    }


@pytest.mark.parametrize("domain", [None, "", "   ", "."])
@pytest.mark.asyncio
async def test_per_domain_mode_requires_domain(domain: str | None) -> None:
    limiter = RateLimiter()

    with pytest.raises(ValueError, match="domain"):
        await limiter.acquire(domain)


@pytest.mark.parametrize(
    "rate",
    [0, -1, True, float("inf"), float("nan"), "1"],
)
def test_invalid_rate_is_rejected(rate: object) -> None:
    with pytest.raises(ValueError, match="requests_per_second"):
        RateLimiter(rate)  # type: ignore[arg-type]


@pytest.mark.parametrize("per_domain", [0, 1, "yes", None])
def test_per_domain_must_be_boolean(per_domain: object) -> None:
    with pytest.raises(ValueError, match="per_domain"):
        RateLimiter(per_domain=per_domain)  # type: ignore[arg-type]


@pytest.mark.parametrize("interval", [-1, True, float("inf"), float("nan")])
@pytest.mark.asyncio
async def test_invalid_min_interval_is_rejected(interval: object) -> None:
    limiter = RateLimiter()

    with pytest.raises(ValueError, match="min_interval"):
        await limiter.acquire(
            "example.com",
            min_interval=interval,  # type: ignore[arg-type]
        )
