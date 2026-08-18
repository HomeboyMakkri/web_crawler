import math

from aiohttp import web
from aiohttp.test_utils import make_mocked_request
import pytest

from src.day5_demo import create_demo_app, run_demo


async def request(app: web.Application, path: str) -> web.Response:
    mocked_request = make_mocked_request("GET", path, app=app)
    match = await app.router.resolve(mocked_request)
    response = await match.handler(mocked_request)
    assert isinstance(response, web.Response)
    return response


@pytest.mark.asyncio
async def test_demo_endpoints_cover_day5_error_scenarios() -> None:
    app, state = create_demo_app(slow_delay=0.0)

    first_429 = await request(app, "/rate-limited")
    recovered = await request(app, "/rate-limited")
    unavailable = await request(app, "/unavailable")
    missing = await request(app, "/missing")
    slow = await request(app, "/slow")

    assert first_429.status == 429
    assert recovered.status == 200
    assert unavailable.status == 503
    assert missing.status == 404
    assert slow.status == 200
    assert state == {
        "rate_limited_requests": 2,
        "unavailable_requests": 1,
        "missing_requests": 1,
        "timeout_requests": 1,
    }


@pytest.mark.parametrize("slow_delay", [-1, True, math.inf, math.nan])
def test_demo_app_validates_slow_delay(slow_delay: object) -> None:
    with pytest.raises(ValueError, match="slow_delay"):
        create_demo_app(slow_delay=slow_delay)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_demo_validates_output_before_opening_socket() -> None:
    with pytest.raises(ValueError, match="output"):
        await run_demo(output="stdout")  # type: ignore[arg-type]
