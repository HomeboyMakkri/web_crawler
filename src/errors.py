"""Domain error types and classification for crawler fetch outcomes."""

from .fetch_result import FetchOutcome, FetchResult


class CrawlerError(Exception):
    """Base class carrying structured context about one crawler failure."""

    retryable = False

    def __init__(
        self,
        message: str,
        *,
        url: str,
        status_code: int | None = None,
        fetch_result: FetchResult | None = None,
        attempt: int | None = None,
    ) -> None:
        normalized_message = self._validate_non_empty_string(message, "message")
        normalized_url = self._validate_non_empty_string(url, "url")
        if fetch_result is not None and not isinstance(fetch_result, FetchResult):
            raise ValueError("fetch_result must be a FetchResult or None")
        if (
            fetch_result is not None
            and fetch_result.url.strip() != normalized_url
        ):
            raise ValueError("fetch_result URL must match error URL")

        result_status = (
            fetch_result.status_code if fetch_result is not None else None
        )
        if status_code is None:
            resolved_status = result_status
        else:
            resolved_status = self._validate_status_code(status_code)
            if result_status is not None and result_status != resolved_status:
                raise ValueError("fetch_result status must match error status")

        if attempt is None:
            resolved_attempt = (
                fetch_result.attempts if fetch_result is not None else 1
            )
        else:
            resolved_attempt = self._validate_attempt(attempt)
        if (
            fetch_result is not None
            and resolved_attempt != fetch_result.attempts
        ):
            raise ValueError("fetch_result attempts must match error attempt")

        super().__init__(normalized_message)
        self.url = normalized_url
        self.status_code = resolved_status
        self.fetch_result = fetch_result
        self.attempt = resolved_attempt

    @staticmethod
    def _validate_non_empty_string(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _validate_status_code(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("status_code must be an integer from 100 to 599")
        if not 100 <= value <= 599:
            raise ValueError("status_code must be an integer from 100 to 599")
        return value

    @staticmethod
    def _validate_attempt(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("attempt must be a positive integer")
        return value


class TransientError(CrawlerError):
    """A temporary server-side or timeout failure that may be retried."""

    retryable = True


class PermanentError(CrawlerError):
    """A failure that repeating the same request is not expected to fix."""


class NetworkError(CrawlerError):
    """A DNS, connection or other network-layer failure that may be retried."""

    retryable = True


class ParseError(CrawlerError):
    """An HTML parsing failure, which is permanent by default."""


def classify_fetch_result(result: FetchResult) -> CrawlerError | None:
    """Convert an unsuccessful HTTP outcome into its Day 5 error category.

    Successful results and robots.txt policy blocks return ``None``. A robots
    block is an intentional crawler decision, not an operational failure.
    """
    if not isinstance(result, FetchResult):
        raise ValueError("result must be a FetchResult")

    if result.outcome in {FetchOutcome.SUCCESS, FetchOutcome.ROBOTS_BLOCKED}:
        return None

    common = {
        "url": result.url,
        "status_code": result.status_code,
        "fetch_result": result,
        "attempt": result.attempts,
    }
    message = result.error or result.outcome.value

    if result.outcome is FetchOutcome.TIMEOUT:
        return TransientError(message, **common)
    if result.outcome is FetchOutcome.NETWORK_ERROR:
        return NetworkError(message, **common)
    if result.outcome is FetchOutcome.HTTP_ERROR:
        status = result.status_code
        if status is None:
            raise RuntimeError("HTTP error FetchResult must contain status_code")
        error_type = (
            TransientError
            if status == 429 or 500 <= status <= 599
            else PermanentError
        )
        return error_type(message, **common)

    raise RuntimeError(f"unsupported fetch outcome: {result.outcome}")
