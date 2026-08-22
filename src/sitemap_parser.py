"""Parse sitemap documents fetched through an injected request callable."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .fetch_result import FetchResult


logger = logging.getLogger(__name__)

SitemapFetcher = Callable[[str], Awaitable[FetchResult]]


class SitemapError(Exception):
    """Base error carrying the sitemap URL that could not be processed."""

    def __init__(self, message: str, *, url: str) -> None:
        super().__init__(f"{message}: {url}")
        self.url = url


class SitemapFetchError(SitemapError):
    """A sitemap document could not be fetched successfully."""


class SitemapParseError(SitemapError):
    """A sitemap response did not contain well-formed XML."""


class SitemapSchemaError(SitemapError):
    """A sitemap XML document had an unsupported root element."""


class SitemapParser:
    """Resolve page URLs from sitemap XML without owning HTTP resources."""

    def __init__(self, fetcher: SitemapFetcher) -> None:
        if not callable(fetcher):
            raise ValueError("fetcher must be callable")
        self._fetcher = fetcher

    async def fetch_sitemap(self, sitemap_url: str) -> list[str]:
        """Fetch and traverse one sitemap, preserving depth-first URL order."""
        root_url = self._normalize_http_url(sitemap_url, field="sitemap_url")
        visited_sitemaps: set[str] = set()
        seen_pages: set[str] = set()
        pages: list[str] = []

        await self._visit_sitemap(
            root_url,
            is_root=True,
            visited_sitemaps=visited_sitemaps,
            seen_pages=seen_pages,
            pages=pages,
        )
        return pages

    async def _visit_sitemap(
        self,
        sitemap_url: str,
        *,
        is_root: bool,
        visited_sitemaps: set[str],
        seen_pages: set[str],
        pages: list[str],
    ) -> None:
        if sitemap_url in visited_sitemaps:
            return
        visited_sitemaps.add(sitemap_url)

        try:
            root = await self._fetch_xml(sitemap_url)
            root_name = self._local_name(root.tag)
            if root_name == "urlset":
                self._collect_page_urls(
                    root,
                    sitemap_url=sitemap_url,
                    seen_pages=seen_pages,
                    pages=pages,
                )
                return
            if root_name == "sitemapindex":
                for child_url in self._collect_child_sitemaps(root, sitemap_url):
                    await self._visit_sitemap(
                        child_url,
                        is_root=False,
                        visited_sitemaps=visited_sitemaps,
                        seen_pages=seen_pages,
                        pages=pages,
                    )
                return
            raise SitemapSchemaError(
                f"unsupported sitemap root element <{root_name}>",
                url=sitemap_url,
            )
        except asyncio.CancelledError:
            raise
        except SitemapError as error:
            if is_root:
                raise
            logger.warning("Skipping nested sitemap %s: %s", sitemap_url, error)

    async def _fetch_xml(self, sitemap_url: str) -> ElementTree.Element:
        try:
            result = await self._fetcher(sitemap_url)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise SitemapFetchError(
                f"sitemap fetch raised {type(error).__name__}: {error}",
                url=sitemap_url,
            ) from error

        if not isinstance(result, FetchResult):
            raise SitemapFetchError(
                "sitemap fetcher did not return FetchResult",
                url=sitemap_url,
            )
        if not result.is_success:
            detail = result.error or result.outcome.value
            raise SitemapFetchError(
                f"sitemap fetch failed ({detail})",
                url=sitemap_url,
            )
        if result.content is None:
            raise SitemapFetchError(
                "successful sitemap fetch contained no body",
                url=sitemap_url,
            )

        try:
            return ElementTree.fromstring(result.content)
        except ElementTree.ParseError as error:
            raise SitemapParseError(
                f"malformed sitemap XML ({error})",
                url=sitemap_url,
            ) from error

    def _collect_page_urls(
        self,
        root: ElementTree.Element,
        *,
        sitemap_url: str,
        seen_pages: set[str],
        pages: list[str],
    ) -> None:
        for url_element in self._children_named(root, "url"):
            location = self._location_text(url_element)
            page_url = self._try_normalize_location(location)
            if page_url is None:
                logger.warning(
                    "Skipping invalid page <loc> in sitemap %s: %r",
                    sitemap_url,
                    location,
                )
                continue
            if page_url not in seen_pages:
                seen_pages.add(page_url)
                pages.append(page_url)

    def _collect_child_sitemaps(
        self,
        root: ElementTree.Element,
        sitemap_url: str,
    ) -> list[str]:
        children: list[str] = []
        for sitemap_element in self._children_named(root, "sitemap"):
            location = self._location_text(sitemap_element)
            child_url = self._try_normalize_location(location)
            if child_url is None:
                logger.warning(
                    "Skipping invalid sitemap <loc> in sitemap %s: %r",
                    sitemap_url,
                    location,
                )
                continue
            children.append(child_url)
        return children

    @classmethod
    def _location_text(cls, parent: ElementTree.Element) -> str | None:
        for child in parent:
            if cls._local_name(child.tag) == "loc":
                return child.text
        return None

    @classmethod
    def _children_named(
        cls,
        parent: ElementTree.Element,
        name: str,
    ) -> list[ElementTree.Element]:
        return [
            child
            for child in parent
            if cls._local_name(child.tag) == name
        ]

    @staticmethod
    def _local_name(tag: object) -> str:
        if not isinstance(tag, str):
            return ""
        return tag.rsplit("}", 1)[-1]

    @classmethod
    def _try_normalize_location(cls, value: str | None) -> str | None:
        try:
            return cls._normalize_http_url(value, field="loc")
        except ValueError:
            return None

    @staticmethod
    def _normalize_http_url(value: object, *, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be an absolute HTTP(S) URL")
        normalized = value.strip()
        try:
            parsed = urlsplit(normalized)
            port = parsed.port
        except ValueError as error:
            raise ValueError(
                f"{field} must be an absolute HTTP(S) URL"
            ) from error
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or parsed.hostname is None
            or any(character.isspace() for character in parsed.netloc)
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError(f"{field} must be an absolute HTTP(S) URL")
        return normalized
