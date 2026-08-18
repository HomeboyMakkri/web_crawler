import logging

from bs4 import BeautifulSoup
import pytest

from src.errors import ParseError
from src.html_parser import HTMLParser


HTML = """
<!doctype html>
<html>
  <head>
    <title> Test   page </title>
    <meta name="description" content="Parser description">
    <meta name="KEYWORDS" content="python, async, crawler">
    <style>body { color: red; }</style>
  </head>
  <body>
    <nav>Navigation</nav>
    <main id="content">
      <h1>Main heading</h1>
      <h2>Second heading</h2>
      <h3>Third heading</h3>
      <p>Hello <strong>async world</strong>.</p>
      <a href="/about">About</a>
      <a href="guide">Guide</a>
      <a href="/about#team">About duplicate</a>
      <a href="https://external.example/news">External</a>
      <a href="mailto:test@example.com">Email</a>
      <a href="javascript:void(0)">JavaScript</a>
      <img src="/images/logo.png" alt="Company logo">
      <img src="https://cdn.example/photo.jpg">
      <table>
        <tr><th>Name</th><th>Score</th></tr>
        <tr><td>Alice</td><td>10</td></tr>
        <tr><td>Bob</td><td>20</td></tr>
      </table>
      <ul><li>First</li><li>Second</li></ul>
      <ol><li>One</li><li>Two</li></ol>
      <script>secretScript()</script>
    </main>
  </body>
</html>
"""


@pytest.mark.asyncio 
async def test_parse_valid_html() -> None:
    parser = HTMLParser()

    result = await parser.parse_html(
        HTML,
        "https://example.com/docs/page.html",
    )

    assert result["url"] == "https://example.com/docs/page.html"
    assert result["title"] == "Test page"
    assert result["metadata"] == {
        "title": "Test page",
        "description": "Parser description",
        "keywords": "python, async, crawler",
    }
    assert result["error"] is None
    assert "Hello async world ." in result["text"]
    assert "secretScript" not in result["text"]
    assert result["links"] == [
        "https://example.com/about",
        "https://example.com/docs/guide",
        "https://external.example/news",
    ]


def test_extract_text_with_selector() -> None:
    parser = HTMLParser()
    soup = BeautifulSoup(HTML, "lxml")

    text = parser.extract_text(soup, "#content p")

    assert text == "Hello async world ."


def test_extract_text_with_invalid_selector(
    caplog: pytest.LogCaptureFixture,
) -> None:
    parser = HTMLParser()
    soup = BeautifulSoup(HTML, "lxml")
    caplog.set_level(logging.WARNING, logger="src.html_parser")

    assert parser.extract_text(soup, "main[") == ""
    assert "Invalid CSS selector" in caplog.text


def test_extract_links_converts_and_validates_urls() -> None:
    parser = HTMLParser()
    soup = BeautifulSoup(
        """
        <a href="../about#team">About</a>
        <a href="https://example.com/about">Duplicate</a>
        <a href="//cdn.example.com/file">CDN</a>
        <a href="mailto:user@example.com">Mail</a>
        <a href="tel:+123456">Phone</a>
        <a href="javascript:void(0)">JS</a>
        <a href="">Empty</a>
        """,
        "lxml",
    )

    links = parser.extract_links(soup, "https://example.com/docs/page")

    assert links == [
        "https://example.com/about",
        "https://cdn.example.com/file",
    ]


def test_extract_links_can_filter_external_domains() -> None:
    parser = HTMLParser(filter_external_links=True)
    soup = BeautifulSoup(
        """
        <a href="/internal">Internal</a>
        <a href="https://other.example/page">External</a>
        """,
        "lxml",
    )

    links = parser.extract_links(soup, "https://example.com/start")

    assert links == ["https://example.com/internal"]


def test_extract_images_headings_tables_and_lists() -> None:
    parser = HTMLParser()
    soup = BeautifulSoup(HTML, "lxml")

    assert parser.extract_images(soup, "https://example.com/page") == [
        {
            "src": "https://example.com/images/logo.png",
            "alt": "Company logo",
        },
        {"src": "https://cdn.example/photo.jpg", "alt": ""},
    ]
    assert parser.extract_headings(soup) == [
        {"level": "h1", "text": "Main heading"},
        {"level": "h2", "text": "Second heading"},
        {"level": "h3", "text": "Third heading"},
    ]
    assert parser.extract_tables(soup) == [
        {
            "headers": ["Name", "Score"],
            "rows": [["Alice", "10"], ["Bob", "20"]],
        }
    ]
    assert parser.extract_lists(soup) == [
        {"type": "ul", "items": ["First", "Second"]},
        {"type": "ol", "items": ["One", "Two"]},
    ]


@pytest.mark.asyncio
async def test_parse_broken_html_returns_partial_result() -> None:
    parser = HTMLParser()
    broken_html = (
        "<html><head><title>Broken</title></head>"
        "<body><div><h1>Still works<a href='/next'>Next"
    )

    result = await parser.parse_html(broken_html, "https://example.com/start")

    assert result["url"] == "https://example.com/start"
    assert result["title"]
    assert result["text"]
    assert result["links"] == ["https://example.com/next"]
    assert result["headings"]


@pytest.mark.asyncio
async def test_extractor_error_keeps_other_partial_data(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    parser = HTMLParser()
    caplog.set_level(logging.WARNING, logger="src.html_parser")

    def broken_link_extractor(*args, **kwargs):
        raise ValueError("broken link")

    monkeypatch.setattr(parser, "extract_links", broken_link_extractor)

    result = await parser.parse_html(HTML, "https://example.com/page")

    assert result["title"] == "Test page"
    assert result["text"]
    assert result["links"] == []
    assert result["images"]
    assert "Could not extract links" in caplog.text

@pytest.mark.asyncio
async def test_parse_valid_html_does_not_log_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    parser = HTMLParser()
    caplog.set_level(logging.WARNING, logger="src.html_parser")

    result = await parser.parse_html(
        "<html><body><p>Test</p></body></html>",
        "https://example.com/page",
    )

    assert result["text"] == "Test"
    assert "Invalid CSS selector" not in caplog.text


@pytest.mark.asyncio
async def test_unexpected_parser_failure_is_converted_to_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = HTMLParser()

    def crash(html: str, url: str) -> dict[str, object]:
        raise RuntimeError("parser implementation crashed")

    monkeypatch.setattr(parser, "_parse_html", crash)

    with pytest.raises(ParseError, match="RuntimeError") as raised:
        await parser.parse_html("<html></html>", "https://example.com/page")

    assert raised.value.url == "https://example.com/page"
    assert isinstance(raised.value.__cause__, RuntimeError)
