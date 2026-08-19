"""Typed outcomes returned by the crawler's HTTP layer."""

import math
from dataclasses import dataclass
from enum import Enum


class FetchOutcome(str, Enum):
    SUCCESS = "success"
    HTTP_ERROR = "http_error"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    ROBOTS_BLOCKED = "robots_blocked"


@dataclass(frozen=True, slots=True)
class FetchResult:
    """One immutable and internally consistent HTTP fetch outcome."""

    url: str
    outcome: FetchOutcome
    content: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    error: str | None = None
    attempts: int = 1
    elapsed_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or not self.url.strip():
            raise ValueError("url must be a non-empty string")
        if not isinstance(self.outcome, FetchOutcome):
            raise ValueError("outcome must be a FetchOutcome")
        if self.content is not None and not isinstance(self.content, str):
            raise ValueError("content must be a string or None")
        if self.content_type is not None and (
            not isinstance(self.content_type, str) or not self.content_type.strip()
        ):
            raise ValueError("content_type must be a non-empty string or None")
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise ValueError("attempts must be a positive integer")
        if self.attempts <= 0:
            raise ValueError("attempts must be a positive integer")
        if (
            isinstance(self.elapsed_seconds, bool)
            or not isinstance(self.elapsed_seconds, (int, float))
            or not math.isfinite(self.elapsed_seconds)
            or self.elapsed_seconds < 0
        ):
            raise ValueError("elapsed_seconds must be a non-negative finite number")

        if self.outcome is FetchOutcome.SUCCESS:
            self._validate_success()
        else:
            self._validate_failure()

    @property
    def is_success(self) -> bool:
        return self.outcome is FetchOutcome.SUCCESS

    @property
    def is_retryable(self) -> bool:
        """Identify outcomes that a future retry policy may retry."""
        if self.outcome in {FetchOutcome.NETWORK_ERROR, FetchOutcome.TIMEOUT}:
            return True
        return (
            self.outcome is FetchOutcome.HTTP_ERROR
            and self.status_code is not None
            and (self.status_code == 429 or 500 <= self.status_code < 600)
        )

    @classmethod
    def success(
        cls,
        url: str,
        content: str,
        *,
        status_code: int = 200,
        content_type: str | None = None,
        attempts: int = 1,
        elapsed_seconds: float = 0.0,
    ) -> "FetchResult":
        return cls(
            url=url,
            outcome=FetchOutcome.SUCCESS,
            content=content,
            status_code=status_code,
            content_type=content_type,
            attempts=attempts,
            elapsed_seconds=elapsed_seconds,
        )

    @classmethod
    def http_error(
        cls,
        url: str,
        status_code: int,
        *,
        error: str | None = None,
        content: str | None = None,
        content_type: str | None = None,
        attempts: int = 1,
        elapsed_seconds: float = 0.0,
    ) -> "FetchResult":
        return cls(
            url=url,
            outcome=FetchOutcome.HTTP_ERROR,
            content=content,
            status_code=status_code,
            content_type=content_type,
            error=error or f"HTTP {status_code}",
            attempts=attempts,
            elapsed_seconds=elapsed_seconds,
        )

    @classmethod
    def network_error(
        cls,
        url: str,
        error: str,
        *,
        attempts: int = 1,
        elapsed_seconds: float = 0.0,
    ) -> "FetchResult":
        return cls(
            url=url,
            outcome=FetchOutcome.NETWORK_ERROR,
            error=error,
            attempts=attempts,
            elapsed_seconds=elapsed_seconds,
        )

    @classmethod
    def timeout(
        cls,
        url: str,
        *,
        error: str = "Request timed out",
        attempts: int = 1,
        elapsed_seconds: float = 0.0,
    ) -> "FetchResult":
        return cls(
            url=url,
            outcome=FetchOutcome.TIMEOUT,
            error=error,
            attempts=attempts,
            elapsed_seconds=elapsed_seconds,
        )

    @classmethod
    def blocked(
        cls,
        url: str,
        *,
        error: str = "Blocked by robots.txt",
    ) -> "FetchResult":
        return cls(
            url=url,
            outcome=FetchOutcome.ROBOTS_BLOCKED,
            error=error,
            elapsed_seconds=0.0,
        )

    def _validate_success(self) -> None:
        if self.content is None:
            raise ValueError("successful result must contain content")
        if self.error is not None:
            raise ValueError("successful result cannot contain an error")
        if not self._is_status_in_range(self.status_code, 200, 400):
            raise ValueError("successful result must have a 2xx or 3xx status code")

    def _validate_failure(self) -> None:
        if not isinstance(self.error, str) or not self.error.strip():
            raise ValueError("failed result must contain a non-empty error")
        if self.outcome is FetchOutcome.HTTP_ERROR:
            if not self._is_status_in_range(self.status_code, 400, 600):
                raise ValueError("HTTP error must have a 4xx or 5xx status code")
        elif self.status_code is not None:
            raise ValueError("non-HTTP failure cannot contain a status code")
        if (
            self.outcome is not FetchOutcome.HTTP_ERROR
            and self.content_type is not None
        ):
            raise ValueError("non-HTTP failure cannot contain a content_type")

    @staticmethod
    def _is_status_in_range(
        status_code: int | None,
        lower: int,
        upper: int,
    ) -> bool:
        return (
            not isinstance(status_code, bool)
            and isinstance(status_code, int)
            and lower <= status_code < upper
        )
