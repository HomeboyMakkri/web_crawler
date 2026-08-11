"""HTML parsing and structured data extraction."""

import logging
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag


logger = logging.getLogger(__name__)


class HTMLParser:
    """Extract crawler-friendly structured data from HTML documents."""

    def __init__(self, *, filter_external_links: bool = False) -> None:
        self._filter_external_links = filter_external_links

    async def parse_html(self, html: str, url: str) -> dict[str, Any]:
        """Parse HTML and return crawler-friendly structured data."""
        return self._parse_html(html, url)

    def _parse_html(self, html: str, url: str) -> dict[str, Any]:
        result = self.empty_result(url)

        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception as error:
            logger.warning(
                "Could not parse HTML from %s: %s",
                url,
                error,
                exc_info=True,
            )
            return result

        extractors: dict[str, tuple[Callable[..., Any], tuple[Any, ...]]] = {
            "text": (self.extract_text, (soup,)),
            "links": (self.extract_links, (soup, url)),
            "metadata": (self.extract_metadata, (soup,)),
            "images": (self.extract_images, (soup, url)),
            "headings": (self.extract_headings, (soup,)),
            "tables": (self.extract_tables, (soup,)),
            "lists": (self.extract_lists, (soup,)),
        }

        for field, (extractor, arguments) in extractors.items():
            try:
                result[field] = extractor(*arguments)
            except Exception as error:
                # One malformed element must not discard data extracted by
                # the other independent extractors.
                logger.warning(
                    "Could not extract %s from %s: %s",
                    field,
                    url,
                    error,
                    exc_info=True,
                )

        result["title"] = result["metadata"].get("title", "")
        return result

    @staticmethod
    def empty_result(url: str, *, error: str | None = None) -> dict[str, Any]:
        """Build a result with a stable schema for failed or empty pages."""
        return {
            "url": url,
            "title": "",
            "text": "",
            "links": [],
            "metadata": {
                "title": "",
                "description": "",
                "keywords": "",
            },
            "images": [],
            "headings": [],
            "tables": [],
            "lists": [],
            "error": error,
        }

    def extract_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        """Return unique, absolute HTTP(S) links in document order."""
        links: list[str] = []
        seen: set[str] = set()
        base_hostname = urlsplit(base_url).hostname

        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href")
            if not isinstance(href, str) or not href.strip():
                continue

            absolute_url, _ = urldefrag(urljoin(base_url, href.strip()))
            if not self._is_valid_http_url(absolute_url):
                continue
            if (
                self._filter_external_links
                and base_hostname is not None
                and urlsplit(absolute_url).hostname != base_hostname
            ):
                continue
            if absolute_url not in seen:
                seen.add(absolute_url)
                links.append(absolute_url)

        return links

    def extract_text(
        self,
        soup: BeautifulSoup,
        selector: str | None = None,
    ) -> str:
        """Extract normalized visible text from the page or CSS selection."""
        if selector is not None:
            try:
                selected = soup.select_one(selector)
            except Exception as error:
                logger.warning("Invalid CSS selector %r: %s", selector, error)
                return ""
            if selected is None:
                return ""
            roots: list[Tag | BeautifulSoup] = [selected]
        else:
            roots = [soup.body or soup]

        ignored_parents = {"script", "style", "noscript", "template"}
        text_parts: list[str] = []
        for root in roots:
            for node in root.find_all(string=True):
                if node.parent is not None and node.parent.name in ignored_parents:
                    continue
                cleaned = self._clean_text(str(node))
                if cleaned:
                    text_parts.append(cleaned)

        return " ".join(text_parts)

    def extract_metadata(self, soup: BeautifulSoup) -> dict[str, str]:
        """Extract title, description and keywords metadata."""
        title = self._clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
        return {
            "title": title,
            "description": self._meta_content(soup, "description"),
            "keywords": self._meta_content(soup, "keywords"),
        }

    def extract_images(
        self,
        soup: BeautifulSoup,
        base_url: str,
    ) -> list[dict[str, str]]:
        """Extract image sources and alternative text."""
        images: list[dict[str, str]] = []

        for image in soup.find_all("img"):
            source = image.get("src", "")
            if not isinstance(source, str):
                source = ""
            source = source.strip()
            absolute_source = urljoin(base_url, source) if source else ""
            images.append(
                {
                    "src": absolute_source,
                    "alt": self._clean_text(str(image.get("alt", ""))),
                }
            )

        return images

    def extract_headings(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        """Extract h1-h3 headings in document order."""
        return [
            {
                "level": heading.name,
                "text": self._clean_text(heading.get_text(" ", strip=True)),
            }
            for heading in soup.find_all(["h1", "h2", "h3"])
        ]

    def extract_tables(self, soup: BeautifulSoup) -> list[dict[str, list]]:
        """Extract table headers and body rows."""
        tables: list[dict[str, list]] = []

        for table in soup.find_all("table"):
            headers: list[str] = []
            rows: list[list[str]] = []

            for row in table.find_all("tr"):
                header_cells = row.find_all("th")
                data_cells = row.find_all("td")
                if header_cells and not data_cells and not headers:
                    headers = [
                        self._clean_text(cell.get_text(" ", strip=True))
                        for cell in header_cells
                    ]
                    continue

                cells = row.find_all(["th", "td"])
                if cells:
                    rows.append(
                        [
                            self._clean_text(cell.get_text(" ", strip=True))
                            for cell in cells
                        ]
                    )

            tables.append({"headers": headers, "rows": rows})

        return tables

    def extract_lists(self, soup: BeautifulSoup) -> list[dict[str, Any]]:
        """Extract ordered and unordered lists."""
        lists: list[dict[str, Any]] = []

        for html_list in soup.find_all(["ul", "ol"]):
            items = [
                self._clean_text(item.get_text(" ", strip=True))
                for item in html_list.find_all("li", recursive=False)
            ]
            lists.append({"type": html_list.name, "items": items})

        return lists

    @staticmethod
    def _meta_content(soup: BeautifulSoup, name: str) -> str:
        meta = soup.find("meta", attrs={"name": re.compile(f"^{name}$", re.I)})
        if meta is None:
            return ""
        content = meta.get("content", "")
        return HTMLParser._clean_text(str(content))

    @staticmethod
    def _is_valid_http_url(url: str) -> bool:
        parsed = urlsplit(url)
        return parsed.scheme in {"http", "https"} and parsed.hostname is not None

    @staticmethod
    def _clean_text(value: str) -> str:
        return " ".join(value.split())
