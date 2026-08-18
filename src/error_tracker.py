"""Collect terminal crawler errors and combine their statistics."""

from collections.abc import Mapping

from .errors import ParseError, classify_fetch_result
from .fetch_result import FetchOutcome, FetchResult


ErrorRecord = dict[str, object]


class ErrorTracker:
    """Own JSON-friendly final errors and non-HTTP error counters."""

    def __init__(self) -> None:
        self._final_errors: dict[str, ErrorRecord] = {}
        self._parse_errors = 0
        self._parse_error_urls: set[str] = set()

    @property
    def final_errors(self) -> dict[str, ErrorRecord]:
        """Expose the current records for compatibility and report storage."""
        return self._final_errors

    def record_fetch_result(self, result: FetchResult) -> None:
        """Remember a terminal fetch failure or clear a recovered URL."""
        if not isinstance(result, FetchResult):
            raise ValueError("result must be a FetchResult")
        if result.is_success:
            self._final_errors.pop(result.url, None)
            return
        if result.outcome is FetchOutcome.ROBOTS_BLOCKED:
            return

        error = classify_fetch_result(result)
        if error is None:
            raise RuntimeError("failed FetchResult must produce a crawler error")
        self._final_errors[result.url] = {
            "url": result.url,
            "error_type": type(error).__name__,
            "outcome": result.outcome.value,
            "status_code": result.status_code,
            "message": result.error or result.outcome.value,
            "attempts": result.attempts,
            "elapsed_seconds": result.elapsed_seconds,
        }

    def record_parse_error(self, error: ParseError) -> None:
        """Count and remember a terminal HTML parsing failure."""
        if not isinstance(error, ParseError):
            raise ValueError("error must be a ParseError")
        self._parse_errors += 1
        self._parse_error_urls.add(error.url)
        self._final_errors[error.url] = {
            "url": error.url,
            "error_type": type(error).__name__,
            "outcome": "parse_error",
            "status_code": None,
            "message": str(error),
            "attempts": error.attempt,
            "elapsed_seconds": 0.0,
        }

    def get_stats(
        self,
        retry_stats: Mapping[str, object],
    ) -> dict[str, object]:
        """Merge retry counters with parsing errors and final records."""
        if not isinstance(retry_stats, Mapping):
            raise ValueError("retry_stats must be a mapping")

        retry_error_counts = retry_stats.get("errors_by_type", {})
        if not isinstance(retry_error_counts, Mapping):
            raise ValueError("retry_stats errors_by_type must be a mapping")
        errors_by_type: dict[str, int] = {}
        for error_name, count in retry_error_counts.items():
            if not isinstance(error_name, str) or not error_name:
                raise ValueError("error type names must be non-empty strings")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("error counts must be non-negative integers")
            errors_by_type[error_name] = count
        if self._parse_errors:
            errors_by_type["ParseError"] = (
                errors_by_type.get("ParseError", 0) + self._parse_errors
            )

        retry_permanent_urls = retry_stats.get("permanent_error_urls", [])
        if not isinstance(retry_permanent_urls, (list, tuple, set)):
            raise ValueError("retry_stats permanent_error_urls must be a sequence")
        normalized_urls: set[str] = set()
        for url in retry_permanent_urls:
            if not isinstance(url, str) or not url.strip():
                raise ValueError("permanent error URLs must be non-empty strings")
            normalized_urls.add(url)
        permanent_urls = sorted(normalized_urls | self._parse_error_urls)

        return {
            **dict(retry_stats),
            "errors_by_type": errors_by_type,
            "permanent_error_urls": permanent_urls,
            "final_errors_count": len(self._final_errors),
            "final_errors": {
                url: dict(record)
                for url, record in self._final_errors.items()
            },
        }

    def clear_final_errors(self) -> None:
        """Clear per-crawl records without resetting lifetime counters."""
        self._final_errors.clear()
