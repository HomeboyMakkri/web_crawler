from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import make_mocked_request
import pytest

from src.crawl_record import CrawlRecord
from src.day6_demo import (
    DEMO_OUTPUT_FILENAMES,
    StorageIntegrityError,
    _prepare_output_directory,
    _record_to_json,
    create_demo_app,
    run_demo,
    verify_storage_integrity,
)


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

    with pytest.raises(ValueError, match="reset_output"):
        await run_demo(reset_output="yes")  # type: ignore[arg-type]


def make_stored_record(
    url: str = "https://example.com",
) -> dict[str, object]:
    return _record_to_json(
        CrawlRecord(
            url=url,
            title="Example",
            text="Page text",
            links=["https://example.com/next"],
            metadata={"description": "Demo"},
            crawled_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
            status_code=200,
            content_type="text/html",
        )
    )


def test_reset_removes_only_known_day6_files(tmp_path: Path) -> None:
    for filename in DEMO_OUTPUT_FILENAMES:
        (tmp_path / filename).write_text("old", encoding="utf-8")
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")

    result = _prepare_output_directory(tmp_path, reset_output=True)

    assert result == tmp_path
    assert all(
        not (tmp_path / filename).exists()
        for filename in DEMO_OUTPUT_FILENAMES
    )
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_reset_output_false_preserves_existing_files(tmp_path: Path) -> None:
    existing = tmp_path / "pages.jsonl"
    existing.write_text("old data", encoding="utf-8")

    _prepare_output_directory(tmp_path, reset_output=False)

    assert existing.read_text(encoding="utf-8") == "old data"


def test_matching_backend_records_pass_integrity_check() -> None:
    record = make_stored_record()
    records_by_backend = {
        "jsonl": [deepcopy(record)],
        "pretty_json": [deepcopy(record)],
        "csv": [deepcopy(record)],
        "sqlite": [deepcopy(record)],
    }

    assert verify_storage_integrity(
        ["https://example.com"],
        records_by_backend,
    ) is True


def test_integrity_check_identifies_backend_url_and_field() -> None:
    record = make_stored_record()
    changed = deepcopy(record)
    changed["title"] = "Different title"

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "backend=csv url=https://example.com field=title mismatch"
        ),
    ):
        verify_storage_integrity(
            ["https://example.com"],
            {
                "jsonl": [record],
                "csv": [changed],
            },
        )


def test_integrity_check_identifies_missing_backend_record() -> None:
    record = make_stored_record()

    with pytest.raises(
        StorageIntegrityError,
        match=(
            r"backend=sqlite URL mismatch: "
            r"missing=\['https://example.com'\] unexpected=\[\]"
        ),
    ):
        verify_storage_integrity(
            ["https://example.com"],
            {
                "jsonl": [record],
                "sqlite": [],
            },
        )
