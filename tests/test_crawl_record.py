from datetime import datetime, timedelta, timezone

import pytest

from src.crawl_record import CrawlRecord
from src.fetch_result import FetchResult
from src.html_parser import HTMLParser


URL = "https://example.com/page"
CRAWLED_AT = datetime(2026, 8, 19, 12, 30, tzinfo=timezone.utc)


def parsed_page() -> dict[str, object]:
    return {
        "url": URL,
        "title": "Example",
        "text": "Stored text",
        "links": ["https://example.com/next"],
        "metadata": {
            "title": "Example",
            "description": "Description",
            "keywords": "crawler",
        },
        "error": None,
    }


def test_record_combines_fetch_and_parse_results() -> None:
    fetch_result = FetchResult.success(
        URL,
        "<html>content</html>",
        status_code=203,
        content_type="Text/HTML",
    )

    record = CrawlRecord.from_fetch_and_parse(
        fetch_result,
        parsed_page(),
        crawled_at=CRAWLED_AT,
    )

    assert record == CrawlRecord(
        url=URL,
        title="Example",
        text="Stored text",
        links=["https://example.com/next"],
        metadata={
            "title": "Example",
            "description": "Description",
            "keywords": "crawler",
        },
        crawled_at=CRAWLED_AT,
        status_code=203,
        content_type="text/html",
    )


@pytest.mark.asyncio
async def test_record_accepts_real_parser_result() -> None:
    html = """
    <html><head><title>Parsed</title></head><body>
      <p>Page text</p><a href="/next">Next</a>
    </body></html>
    """
    parsed = await HTMLParser().parse_html(html, URL)
    fetch_result = FetchResult.success(
        URL,
        html,
        content_type="text/html",
    )

    record = CrawlRecord.from_fetch_and_parse(
        fetch_result,
        parsed,
        crawled_at=CRAWLED_AT,
    )

    assert record.title == "Parsed"
    assert record.text == "Page text Next"
    assert record.links == ["https://example.com/next"]


def test_unknown_content_type_uses_binary_fallback() -> None:
    record = CrawlRecord.from_fetch_and_parse(
        FetchResult.success(URL, "content"),
        parsed_page(),
        crawled_at=CRAWLED_AT,
    )

    assert record.content_type == "application/octet-stream"


def test_record_copies_mutable_parser_data_and_to_dict_is_detached() -> None:
    parsed = parsed_page()
    record = CrawlRecord.from_fetch_and_parse(
        FetchResult.success(URL, "content"),
        parsed,
        crawled_at=CRAWLED_AT,
    )

    parsed_links = parsed["links"]
    assert isinstance(parsed_links, list)
    parsed_links.append("https://example.com/changed")
    parsed_metadata = parsed["metadata"]
    assert isinstance(parsed_metadata, dict)
    parsed_metadata["title"] = "Changed"
    serialized = record.to_dict()
    serialized_links = serialized["links"]
    assert isinstance(serialized_links, list)
    serialized_links.append("https://example.com/serialized")

    assert record.links == ["https://example.com/next"]
    assert record.metadata["title"] == "Example"
    assert record.to_dict()["links"] == ["https://example.com/next"]


def test_crawled_at_is_normalized_to_utc() -> None:
    plus_three = timezone(timedelta(hours=3))
    local_time = datetime(2026, 8, 19, 15, 30, tzinfo=plus_three)

    record = CrawlRecord.from_fetch_and_parse(
        FetchResult.success(URL, "content"),
        parsed_page(),
        crawled_at=local_time,
    )

    assert record.crawled_at == CRAWLED_AT
    assert record.crawled_at.tzinfo is timezone.utc


@pytest.mark.parametrize(
    ("fetch_result", "parsed", "message"),
    [
        (FetchResult.http_error(URL, 404), parsed_page(), "successful"),
        (
            FetchResult.success(URL, "content"),
            {**parsed_page(), "url": "https://other.example"},
            "URL",
        ),
        (
            FetchResult.success(URL, "content"),
            {**parsed_page(), "error": "parse failed"},
            "must not contain an error",
        ),
        (
            FetchResult.success(URL, "content"),
            {key: value for key, value in parsed_page().items() if key != "text"},
            "missing 'text'",
        ),
        (
            FetchResult.success(URL, "content"),
            {**parsed_page(), "links": "https://example.com/next"},
            "links",
        ),
    ],
)
def test_invalid_source_results_are_rejected(
    fetch_result: FetchResult,
    parsed: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        CrawlRecord.from_fetch_and_parse(fetch_result, parsed)


def test_record_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        CrawlRecord.from_fetch_and_parse(
            FetchResult.success(URL, "content"),
            parsed_page(),
            crawled_at=datetime(2026, 8, 19, 12, 30),
        )
