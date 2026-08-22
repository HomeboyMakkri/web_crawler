import asyncio
import logging
from collections.abc import Awaitable, Callable

import pytest

from src.fetch_result import FetchResult
from src.sitemap_parser import (
    SitemapFetchError,
    SitemapParseError,
    SitemapParser,
    SitemapSchemaError,
)


ROOT_URL = "https://example.com/sitemap.xml"


def successful_fetcher(
    documents: dict[str, str],
    calls: list[str] | None = None,
) -> Callable[[str], Awaitable[FetchResult]]:
    async def fetch(url: str) -> FetchResult:
        if calls is not None:
            calls.append(url)
        return FetchResult.success(
            url,
            documents[url],
            content_type="text/plain",
        )

    return fetch


@pytest.mark.asyncio
async def test_parses_urlset_and_strips_location_whitespace() -> None:
    document = """
    <urlset>
        <url><loc> https://example.com/one </loc></url>
        <url><loc>https://example.com/two</loc></url>
    </urlset>
    """
    parser = SitemapParser(successful_fetcher({ROOT_URL: document}))

    result = await parser.fetch_sitemap(ROOT_URL)

    assert result == [
        "https://example.com/one",
        "https://example.com/two",
    ]


@pytest.mark.asyncio
async def test_parses_namespaced_urlset_by_local_element_names() -> None:
    document = """
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>https://example.com/namespaced</loc></url>
    </urlset>
    """
    parser = SitemapParser(successful_fetcher({ROOT_URL: document}))

    assert await parser.fetch_sitemap(ROOT_URL) == [
        "https://example.com/namespaced"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("root_name", ["urlset", "sitemapindex"])
async def test_empty_valid_sitemap_returns_empty_list(root_name: str) -> None:
    parser = SitemapParser(
        successful_fetcher({ROOT_URL: f"<{root_name}></{root_name}>"})
    )

    assert await parser.fetch_sitemap(ROOT_URL) == []


@pytest.mark.asyncio
async def test_recurses_through_indexes_in_depth_first_document_order() -> None:
    first = "https://example.com/first.xml"
    nested_index = "https://example.com/nested-index.xml"
    nested_page = "https://example.com/nested-page.xml"
    last = "https://example.com/last.xml"
    documents = {
        ROOT_URL: f"""
            <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                <sitemap><loc>{first}</loc></sitemap>
                <sitemap><loc>{nested_index}</loc></sitemap>
                <sitemap><loc>{last}</loc></sitemap>
            </sitemapindex>
        """,
        first: """
            <urlset>
                <url><loc>https://example.com/one</loc></url>
                <url><loc>https://example.com/two</loc></url>
            </urlset>
        """,
        nested_index: f"""
            <sitemapindex>
                <sitemap><loc>{nested_page}</loc></sitemap>
            </sitemapindex>
        """,
        nested_page: """
            <urlset><url><loc>https://example.com/three</loc></url></urlset>
        """,
        last: """
            <urlset><url><loc>https://example.com/four</loc></url></urlset>
        """,
    }
    calls: list[str] = []
    parser = SitemapParser(successful_fetcher(documents, calls))

    result = await parser.fetch_sitemap(ROOT_URL)

    assert result == [
        "https://example.com/one",
        "https://example.com/two",
        "https://example.com/three",
        "https://example.com/four",
    ]
    assert calls == [ROOT_URL, first, nested_index, nested_page, last]


@pytest.mark.asyncio
async def test_duplicate_page_urls_keep_only_first_seen_position() -> None:
    document = """
    <urlset>
        <url><loc>https://example.com/one</loc></url>
        <url><loc>https://example.com/two</loc></url>
        <url><loc>https://example.com/one</loc></url>
    </urlset>
    """
    parser = SitemapParser(successful_fetcher({ROOT_URL: document}))

    assert await parser.fetch_sitemap(ROOT_URL) == [
        "https://example.com/one",
        "https://example.com/two",
    ]


@pytest.mark.asyncio
async def test_repeated_child_sitemap_is_fetched_once() -> None:
    child = "https://example.com/child.xml"
    documents = {
        ROOT_URL: f"""
            <sitemapindex>
                <sitemap><loc>{child}</loc></sitemap>
                <sitemap><loc>{child}</loc></sitemap>
            </sitemapindex>
        """,
        child: """
            <urlset><url><loc>https://example.com/page</loc></url></urlset>
        """,
    }
    calls: list[str] = []
    parser = SitemapParser(successful_fetcher(documents, calls))

    assert await parser.fetch_sitemap(ROOT_URL) == ["https://example.com/page"]
    assert calls == [ROOT_URL, child]


@pytest.mark.asyncio
async def test_sitemap_cycle_is_ignored_after_first_discovery() -> None:
    cyclic_child = "https://example.com/cycle.xml"
    page_child = "https://example.com/pages.xml"
    documents = {
        ROOT_URL: f"""
            <sitemapindex>
                <sitemap><loc>{cyclic_child}</loc></sitemap>
                <sitemap><loc>{page_child}</loc></sitemap>
            </sitemapindex>
        """,
        cyclic_child: f"""
            <sitemapindex><sitemap><loc>{ROOT_URL}</loc></sitemap></sitemapindex>
        """,
        page_child: """
            <urlset><url><loc>https://example.com/page</loc></url></urlset>
        """,
    }
    calls: list[str] = []
    parser = SitemapParser(successful_fetcher(documents, calls))

    assert await parser.fetch_sitemap(ROOT_URL) == ["https://example.com/page"]
    assert calls == [ROOT_URL, cyclic_child, page_child]


@pytest.mark.asyncio
async def test_invalid_page_locations_are_skipped_with_warnings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    document = """
    <urlset>
        <url><loc>relative/page</loc></url>
        <url><loc>ftp://example.com/page</loc></url>
        <url><loc>https://example.com:bad/page</loc></url>
        <url><loc>   </loc></url>
        <url></url>
        <url><loc>http://example.com/valid</loc></url>
    </urlset>
    """
    parser = SitemapParser(successful_fetcher({ROOT_URL: document}))

    with caplog.at_level(logging.WARNING, logger="src.sitemap_parser"):
        result = await parser.fetch_sitemap(ROOT_URL)

    assert result == ["http://example.com/valid"]
    assert caplog.text.count("Skipping invalid page <loc>") == 5


@pytest.mark.asyncio
async def test_invalid_child_sitemap_location_is_skipped_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    valid_child = "https://example.com/valid.xml"
    documents = {
        ROOT_URL: f"""
            <sitemapindex>
                <sitemap><loc>/relative.xml</loc></sitemap>
                <sitemap><loc>{valid_child}</loc></sitemap>
            </sitemapindex>
        """,
        valid_child: "<urlset></urlset>",
    }
    parser = SitemapParser(successful_fetcher(documents))

    with caplog.at_level(logging.WARNING, logger="src.sitemap_parser"):
        result = await parser.fetch_sitemap(ROOT_URL)

    assert result == []
    assert "Skipping invalid sitemap <loc>" in caplog.text
    assert ROOT_URL in caplog.text


@pytest.mark.asyncio
async def test_failed_root_fetch_raises_typed_error_with_source_url() -> None:
    async def fetch(url: str) -> FetchResult:
        return FetchResult.http_error(url, 503)

    parser = SitemapParser(fetch)

    with pytest.raises(SitemapFetchError, match=ROOT_URL) as error:
        await parser.fetch_sitemap(ROOT_URL)

    assert error.value.url == ROOT_URL
    assert "HTTP 503" in str(error.value)


@pytest.mark.asyncio
async def test_root_fetch_exception_is_wrapped_as_typed_error() -> None:
    async def fetch(url: str) -> FetchResult:
        raise RuntimeError("fetch dependency failed")

    parser = SitemapParser(fetch)

    with pytest.raises(SitemapFetchError, match="fetch dependency failed") as error:
        await parser.fetch_sitemap(ROOT_URL)

    assert error.value.url == ROOT_URL
    assert isinstance(error.value.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_malformed_root_xml_raises_typed_error_with_source_url() -> None:
    parser = SitemapParser(
        successful_fetcher({ROOT_URL: "<urlset><url></urlset>"})
    )

    with pytest.raises(SitemapParseError, match=ROOT_URL) as error:
        await parser.fetch_sitemap(ROOT_URL)

    assert error.value.url == ROOT_URL


@pytest.mark.asyncio
async def test_unsupported_root_schema_raises_typed_error_with_source_url() -> None:
    parser = SitemapParser(successful_fetcher({ROOT_URL: "<feed></feed>"}))

    with pytest.raises(SitemapSchemaError, match=ROOT_URL) as error:
        await parser.fetch_sitemap(ROOT_URL)

    assert error.value.url == ROOT_URL
    assert "feed" in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["fetch", "parse", "schema"])
async def test_nested_failure_logs_warning_and_preserves_sibling_results(
    failure: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    broken = "https://example.com/broken.xml"
    valid = "https://example.com/valid.xml"
    root_document = f"""
        <sitemapindex>
            <sitemap><loc>{broken}</loc></sitemap>
            <sitemap><loc>{valid}</loc></sitemap>
        </sitemapindex>
    """
    documents = {
        ROOT_URL: root_document,
        broken: (
            "<urlset><url></urlset>"
            if failure == "parse"
            else "<feed></feed>"
        ),
        valid: """
            <urlset><url><loc>https://example.com/page</loc></url></urlset>
        """,
    }

    async def fetch(url: str) -> FetchResult:
        if failure == "fetch" and url == broken:
            return FetchResult.timeout(url)
        return FetchResult.success(url, documents[url])

    parser = SitemapParser(fetch)

    with caplog.at_level(logging.WARNING, logger="src.sitemap_parser"):
        result = await parser.fetch_sitemap(ROOT_URL)

    assert result == ["https://example.com/page"]
    assert "Skipping nested sitemap" in caplog.text
    assert broken in caplog.text


@pytest.mark.asyncio
async def test_cancellation_from_nested_fetch_is_propagated() -> None:
    child = "https://example.com/child.xml"

    async def fetch(url: str) -> FetchResult:
        if url == child:
            raise asyncio.CancelledError
        return FetchResult.success(
            url,
            f"<sitemapindex><sitemap><loc>{child}</loc></sitemap></sitemapindex>",
        )

    parser = SitemapParser(fetch)

    with pytest.raises(asyncio.CancelledError):
        await parser.fetch_sitemap(ROOT_URL)


@pytest.mark.asyncio
async def test_traversal_state_and_return_value_are_detached_between_calls() -> None:
    calls: list[str] = []
    document = """
        <urlset><url><loc>https://example.com/page</loc></url></urlset>
    """
    parser = SitemapParser(successful_fetcher({ROOT_URL: document}, calls))

    first = await parser.fetch_sitemap(ROOT_URL)
    first.append("https://mutated.example")
    second = await parser.fetch_sitemap(ROOT_URL)

    assert second == ["https://example.com/page"]
    assert calls == [ROOT_URL, ROOT_URL]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "",
        "relative/sitemap.xml",
        "ftp://example.com/sitemap.xml",
        "https:///missing-host.xml",
        "https://example.com:bad/sitemap.xml",
        "https://example.com:70000/sitemap.xml",
    ],
)
async def test_invalid_root_sitemap_url_is_rejected_before_fetch(url: str) -> None:
    calls: list[str] = []
    parser = SitemapParser(successful_fetcher({}, calls))

    with pytest.raises(ValueError, match="sitemap_url"):
        await parser.fetch_sitemap(url)

    assert calls == []


def test_fetcher_must_be_callable() -> None:
    with pytest.raises(ValueError, match="fetcher"):
        SitemapParser("not callable")  # type: ignore[arg-type]
