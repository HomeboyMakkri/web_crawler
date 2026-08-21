from aiohttp import web
from aiohttp.test_utils import make_mocked_request
import pytest

from src.day6_demo import create_demo_app, run_demo


async def request(app: web.Application, path: str) -> web.Response:
    mocked_request = make_mocked_request("GET", path, app=app)
    match = await app.router.resolve(mocked_request)
    response = await match.handler(mocked_request)
    assert isinstance(response, web.Response)
    return response


async def test_demo_site_contains_three_local_html_pages() -> None:
    app = create_demo_app()

    index = await request(app, "/")
    json_page = await request(app, "/json")
    database_page = await request(app, "/database")

    index_text = index.text
    json_text = json_page.text
    database_text = database_page.text

    assert index.status == 200
    assert index_text is not None
    assert json_text is not None
    assert database_text is not None
    assert "/json" in index_text
    assert "/database" in index_text
    assert "JSON Lines" in json_text
    assert "SQLite" in database_text


async def test_demo_validates_output_before_opening_socket() -> None:
    with pytest.raises(ValueError, match="output"):
        await run_demo(output="stdout")  # type: ignore[arg-type]
