"""URL admission rules for recursive crawling."""

import re
from collections.abc import Iterable
from re import Pattern
from urllib.parse import SplitResult, urlsplit


class URLFilter:
    """Decide whether a discovered URL may be scheduled for crawling."""

    def __init__(
        self,
        *,
        same_domain_only: bool = False,
        allowed_domains: Iterable[str] | None = None,
        include_patterns: Iterable[str] | None = None,
        exclude_patterns: Iterable[str] | None = None,
    ) -> None:
        if not isinstance(same_domain_only, bool):
            raise ValueError("same_domain_only must be a boolean")

        self._same_domain_only = same_domain_only
        self._allowed_domains = self._normalize_domains(allowed_domains or ())
        if self._same_domain_only and not self._allowed_domains:
            raise ValueError(
                "allowed_domains must not be empty when same_domain_only is enabled"
            )

        self._include_patterns = self._compile_patterns(
            include_patterns or (),
            "include_patterns",
        )
        self._exclude_patterns = self._compile_patterns(
            exclude_patterns or (),
            "exclude_patterns",
        )

    @classmethod
    def from_start_urls(
        cls,
        start_urls: Iterable[str],
        *,
        same_domain_only: bool = False,
        include_patterns: Iterable[str] | None = None,
        exclude_patterns: Iterable[str] | None = None,
    ) -> "URLFilter":
        """Create a filter whose allowed domains come from starting URLs."""
        urls = list(start_urls)
        domains: set[str] = set()

        for url in urls:
            parsed = cls._parse_http_url(url)
            if parsed is None:
                raise ValueError(f"invalid start URL: {url!r}")
            hostname = parsed.hostname
            if hostname is None:  # Kept explicit for static type checkers.
                raise ValueError(f"invalid start URL: {url!r}")
            domains.add(hostname.lower())

        if not urls:
            raise ValueError("start_urls must not be empty")

        return cls(
            same_domain_only=same_domain_only,
            allowed_domains=domains,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
        )

    def should_crawl(self, url: str) -> bool:
        """Return whether a URL passes scheme, domain and pattern rules."""
        parsed = self._parse_http_url(url)
        if parsed is None:
            return False
        hostname = parsed.hostname
        if hostname is None:  # Kept explicit for static type checkers.
            return False

        if (
            self._same_domain_only
            and hostname.lower() not in self._allowed_domains
        ):
            return False

        candidate = url.strip()
        if any(pattern.search(candidate) for pattern in self._exclude_patterns):
            return False

        if self._include_patterns and not any(
            pattern.search(candidate) for pattern in self._include_patterns
        ):
            return False

        return True

    @property
    def allowed_domains(self) -> frozenset[str]:
        return frozenset(self._allowed_domains)

    @staticmethod
    def _parse_http_url(url: str) -> SplitResult | None:
        if not isinstance(url, str) or not url.strip():
            return None

        try:
            parsed = urlsplit(url.strip())
            # Accessing port also validates malformed values such as ``:abc``.
            parsed.port
        except ValueError:
            return None

        if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname is None:
            return None
        return parsed

    @classmethod
    def _normalize_domains(cls, domains: Iterable[str]) -> set[str]:
        normalized: set[str] = set()
        for domain in domains:
            if not isinstance(domain, str) or not domain.strip():
                raise ValueError("allowed_domains must contain non-empty strings")

            parsed = urlsplit(f"//{domain.strip()}")
            try:
                parsed.port
            except ValueError as error:
                raise ValueError(f"invalid allowed domain: {domain!r}") from error

            if parsed.hostname is None or parsed.path or parsed.query or parsed.fragment:
                raise ValueError(f"invalid allowed domain: {domain!r}")
            normalized.add(parsed.hostname.lower())
        return normalized

    @staticmethod
    def _compile_patterns(
        patterns: Iterable[str],
        parameter_name: str,
    ) -> tuple[Pattern[str], ...]:
        compiled: list[Pattern[str]] = []
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(f"{parameter_name} must contain non-empty strings")
            try:
                compiled.append(re.compile(pattern))
            except re.error as error:
                raise ValueError(
                    f"invalid regular expression in {parameter_name}: {pattern!r}"
                ) from error
        return tuple(compiled)
