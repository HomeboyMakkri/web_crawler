"""Standardized model passed from crawling to persistent storage."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast

from .fetch_result import FetchResult


@dataclass(frozen=True, slots=True)
class CrawlRecord:
    """One successfully fetched and parsed page ready for persistence."""

    url: str
    title: str
    text: str
    links: list[str]
    metadata: dict[str, str]
    crawled_at: datetime
    status_code: int
    content_type: str

    def __post_init__(self) -> None:
        self._validate_non_empty_string(self.url, "url")
        self._validate_string(self.title, "title")
        self._validate_string(self.text, "text")
        if not isinstance(self.links, list) or any(
            not isinstance(link, str) or not link.strip()
            for link in self.links
        ):
            raise ValueError("links must be a list of non-empty strings")
        if not isinstance(self.metadata, dict) or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            for key, value in self.metadata.items()
        ):
            raise ValueError("metadata must be a dictionary of strings")
        if (
            not isinstance(self.crawled_at, datetime)
            or self.crawled_at.tzinfo is None
            or self.crawled_at.utcoffset() is None
        ):
            raise ValueError("crawled_at must be a timezone-aware datetime")
        if (
            isinstance(self.status_code, bool)
            or not isinstance(self.status_code, int)
            or not 200 <= self.status_code < 400
        ):
            raise ValueError("status_code must be a successful HTTP status")
        self._validate_non_empty_string(self.content_type, "content_type")

        # Do not retain mutable containers owned by the parser result.
        object.__setattr__(self, "url", self.url.strip())
        object.__setattr__(self, "links", list(self.links))
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(
            self,
            "crawled_at",
            self.crawled_at.astimezone(timezone.utc),
        )
        object.__setattr__(self, "content_type", self.content_type.strip().lower())

    @classmethod
    def from_fetch_and_parse(
        cls,
        fetch_result: FetchResult,
        parsed: Mapping[str, object],
        *,
        crawled_at: datetime | None = None,
    ) -> "CrawlRecord":
        """Combine one successful HTTP result and its parsed representation."""
        if not isinstance(fetch_result, FetchResult):
            raise ValueError("fetch_result must be a FetchResult")
        if not fetch_result.is_success:
            raise ValueError("fetch_result must be successful")
        if not isinstance(parsed, Mapping):
            raise ValueError("parsed must be a mapping")

        parsed_url = parsed.get("url")
        if parsed_url != fetch_result.url:
            raise ValueError("parsed URL must match fetch_result URL")
        if parsed.get("error") is not None:
            raise ValueError("parsed result must not contain an error")

        try:
            title = parsed["title"]
            text = parsed["text"]
            links = parsed["links"]
            metadata = parsed["metadata"]
        except KeyError as error:
            raise ValueError(f"parsed result is missing {error.args[0]!r}") from error

        if not isinstance(title, str):
            raise ValueError("parsed title must be a string")
        if not isinstance(text, str):
            raise ValueError("parsed text must be a string")
        if not isinstance(links, list):
            raise ValueError("parsed links must be a list")
        if not isinstance(metadata, dict):
            raise ValueError("parsed metadata must be a dictionary")
        if fetch_result.status_code is None:
            raise RuntimeError("successful FetchResult must contain status_code")

        return cls(
            url=fetch_result.url,
            title=title,
            text=text,
            links=cast(list[str], links),
            metadata=cast(dict[str, str], metadata),
            crawled_at=crawled_at or datetime.now(timezone.utc),
            status_code=fetch_result.status_code,
            content_type=(
                fetch_result.content_type or "application/octet-stream"
            ),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a detached dictionary while keeping crawled_at as datetime."""
        return {
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "links": list(self.links),
            "metadata": dict(self.metadata),
            "crawled_at": self.crawled_at,
            "status_code": self.status_code,
            "content_type": self.content_type,
        }

    @staticmethod
    def _validate_string(value: object, name: str) -> None:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")

    @staticmethod
    def _validate_non_empty_string(value: object, name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
