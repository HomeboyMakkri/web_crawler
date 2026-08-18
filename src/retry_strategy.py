"""Generic asynchronous retry execution with typed error policies."""

import asyncio
import logging
import math
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from numbers import Real
from typing import Any, TypeVar, TypedDict

from .errors import NetworkError, PermanentError, TransientError


logger = logging.getLogger(__name__)

ResultT = TypeVar("ResultT")
Sleep = Callable[[float], Awaitable[None]]
ErrorType = type[Exception]


class RetryStrategyStats(TypedDict):
    total_executions: int
    total_attempts: int
    errors_by_type: dict[str, int]
    scheduled_retries: int
    retries_by_type: dict[str, int]
    successful_retries: int
    exhausted_retries: int
    non_retryable_failures: int
    total_backoff_time: float
    average_backoff_time: float
    average_retry_wait: float
    permanent_error_urls: list[str]


class RetryStrategy:
    """Execute an async callable again after configured exception types.

    ``max_retries`` counts additional calls after the initial attempt. Type
    limits may reduce this global ceiling but can never increase it.
    """

    def __init__(
        self,
        max_retries: int = 3,
        backoff_factor: float = 2.0,
        retry_on: list[ErrorType] | tuple[ErrorType, ...] | None = None,
        *,
        base_delay: float = 0.5,
        max_delay: float = 30.0,
        retry_limits: Mapping[ErrorType, int] | None = None,
        backoff_factors: Mapping[ErrorType, float] | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._max_retries = self._validate_non_negative_int(
            max_retries,
            "max_retries",
        )
        self._backoff_factor = self._validate_backoff_factor(
            backoff_factor,
            "backoff_factor",
        )
        self._base_delay = self._validate_positive_number(
            base_delay,
            "base_delay",
        )
        self._max_delay = self._validate_positive_number(
            max_delay,
            "max_delay",
        )
        if self._max_delay < self._base_delay:
            raise ValueError("max_delay must be greater than or equal to base_delay")

        self._retry_on = self._normalize_retry_on(retry_on)
        self._retry_limits = self._normalize_retry_limits(retry_limits)
        self._backoff_factors = self._normalize_backoff_factors(backoff_factors)
        if not callable(sleep):
            raise ValueError("sleep must be callable")
        self._sleep = sleep

        self._total_executions = 0
        self._total_attempts = 0
        self._errors_by_type: defaultdict[str, int] = defaultdict(int)
        self._scheduled_retries = 0
        self._retries_by_type: defaultdict[str, int] = defaultdict(int)
        self._successful_retries = 0
        self._exhausted_retries = 0
        self._non_retryable_failures = 0
        self._total_backoff_time = 0.0
        self._permanent_error_urls: set[str] = set()

    @property
    def max_retries(self) -> int:
        return self._max_retries

    async def execute_with_retry(
        self,
        coro: Callable[..., Awaitable[ResultT]],
        *args: Any,
        **kwargs: Any,
    ) -> ResultT:
        """Execute a fresh coroutine call on every attempt."""
        if not callable(coro):
            raise ValueError("coro must be an async callable")

        self._total_executions += 1
        total_retries = 0
        retries_by_policy_type: defaultdict[ErrorType, int] = defaultdict(int)

        while True:
            attempt = total_retries + 1
            self._total_attempts += 1
            try:
                result = await coro(*args, **kwargs)
            except Exception as error:
                error_name = type(error).__name__
                self._errors_by_type[error_name] += 1
                self._remember_permanent_url(error)

                if not isinstance(error, self._retry_on):
                    self._non_retryable_failures += 1
                    logger.error(
                        "Request failed without retry: type=%s url=%s attempt=%d",
                        error_name,
                        self._get_error_url(error),
                        attempt,
                    )
                    raise

                retry_limit, policy_type = self._get_retry_limit(error)
                type_retries = retries_by_policy_type[policy_type]
                if total_retries >= self._max_retries or type_retries >= retry_limit:
                    self._exhausted_retries += 1
                    logger.error(
                        "Retry exhausted: type=%s url=%s attempt=%d retries=%d",
                        error_name,
                        self._get_error_url(error),
                        attempt,
                        total_retries,
                    )
                    raise

                type_retry_number = type_retries + 1
                delay = self._calculate_delay(error, type_retry_number)
                total_retries += 1
                retries_by_policy_type[policy_type] += 1
                self._scheduled_retries += 1
                self._retries_by_type[error_name] += 1
                self._total_backoff_time += delay
                logger.warning(
                    "Retry scheduled: type=%s url=%s attempt=%d "
                    "retry=%d/%d delay=%.3fs",
                    error_name,
                    self._get_error_url(error),
                    attempt,
                    total_retries,
                    self._max_retries,
                    delay,
                )
                await self._sleep(delay)
                continue

            if total_retries > 0:
                self._successful_retries += 1
                logger.info(
                    "Retry succeeded: attempts=%d retries=%d",
                    attempt,
                    total_retries,
                )
            return result

    def get_stats(self) -> RetryStrategyStats:
        """Return a detached snapshot accumulated across all executions."""
        average_backoff = (
            self._total_backoff_time / self._scheduled_retries
            if self._scheduled_retries
            else 0.0
        )
        return {
            "total_executions": self._total_executions,
            "total_attempts": self._total_attempts,
            "errors_by_type": dict(self._errors_by_type),
            "scheduled_retries": self._scheduled_retries,
            "retries_by_type": dict(self._retries_by_type),
            "successful_retries": self._successful_retries,
            "exhausted_retries": self._exhausted_retries,
            "non_retryable_failures": self._non_retryable_failures,
            "total_backoff_time": self._total_backoff_time,
            "average_backoff_time": average_backoff,
            "average_retry_wait": average_backoff,
            "permanent_error_urls": sorted(self._permanent_error_urls),
        }

    def _get_retry_limit(self, error: Exception) -> tuple[int, ErrorType]:
        configured = self._resolve_type_setting(error, self._retry_limits)
        if configured is None:
            return self._max_retries, type(error)
        policy_type, limit = configured
        return limit, policy_type

    def _calculate_delay(self, error: Exception, retry_number: int) -> float:
        configured = self._resolve_type_setting(error, self._backoff_factors)
        factor = self._backoff_factor if configured is None else configured[1]
        exponent = retry_number - 1
        logarithmic_delay = math.log(self._base_delay) + exponent * math.log(factor)
        if logarithmic_delay >= math.log(self._max_delay):
            return self._max_delay
        try:
            delay = self._base_delay * (factor**exponent)
        except OverflowError:
            # The factor may overflow before multiplication by a tiny base.
            delay = math.exp(logarithmic_delay)
        return min(delay, self._max_delay)

    @staticmethod
    def _resolve_type_setting(
        error: Exception,
        settings: Mapping[ErrorType, ResultT],
    ) -> tuple[ErrorType, ResultT] | None:
        for error_type in type(error).__mro__:
            if error_type in settings:
                return error_type, settings[error_type]
        return None

    def _normalize_retry_on(
        self,
        value: list[ErrorType] | tuple[ErrorType, ...] | None,
    ) -> tuple[ErrorType, ...]:
        candidates = (
            (TransientError, NetworkError)
            if value is None
            else value
        )
        if not isinstance(candidates, (list, tuple)):
            raise ValueError("retry_on must be a list or tuple of exception types")
        normalized: list[ErrorType] = []
        for error_type in candidates:
            self._validate_error_type(error_type, "retry_on")
            if error_type not in normalized:
                normalized.append(error_type)
        return tuple(normalized)

    def _normalize_retry_limits(
        self,
        values: Mapping[ErrorType, int] | None,
    ) -> dict[ErrorType, int]:
        if values is None:
            return {}
        if not isinstance(values, Mapping):
            raise ValueError("retry_limits must be a mapping")
        normalized: dict[ErrorType, int] = {}
        for error_type, limit in values.items():
            self._validate_policy_error_type(error_type, "retry_limits")
            validated_limit = self._validate_non_negative_int(
                limit,
                "retry limit",
            )
            if validated_limit > self._max_retries:
                raise ValueError("retry limit cannot exceed max_retries")
            normalized[error_type] = validated_limit
        return normalized

    def _normalize_backoff_factors(
        self,
        values: Mapping[ErrorType, float] | None,
    ) -> dict[ErrorType, float]:
        if values is None:
            return {}
        if not isinstance(values, Mapping):
            raise ValueError("backoff_factors must be a mapping")
        normalized: dict[ErrorType, float] = {}
        for error_type, factor in values.items():
            self._validate_policy_error_type(error_type, "backoff_factors")
            normalized[error_type] = self._validate_backoff_factor(
                factor,
                "backoff factor",
            )
        return normalized

    def _validate_policy_error_type(
        self,
        error_type: object,
        name: str,
    ) -> ErrorType:
        validated = self._validate_error_type(error_type, name)
        if not any(issubclass(validated, allowed) for allowed in self._retry_on):
            raise ValueError(f"{name} type must be included in retry_on")
        return validated

    @staticmethod
    def _validate_error_type(value: object, name: str) -> ErrorType:
        if (
            not isinstance(value, type)
            or not issubclass(value, Exception)
        ):
            raise ValueError(f"{name} must contain Exception types")
        return value

    @staticmethod
    def _get_error_url(error: Exception) -> str:
        url = getattr(error, "url", None)
        return url if isinstance(url, str) and url else "<unknown>"

    def _remember_permanent_url(self, error: Exception) -> None:
        if not isinstance(error, PermanentError):
            return
        url = self._get_error_url(error)
        if url != "<unknown>":
            self._permanent_error_urls.add(url)

    @staticmethod
    def _validate_non_negative_int(value: int, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
        return value

    @staticmethod
    def _validate_positive_number(value: float, name: str) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{name} must be a positive finite number")
        return float(value)

    @staticmethod
    def _validate_backoff_factor(value: float, name: str) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(value)
            or value < 1
        ):
            raise ValueError(f"{name} must be a finite number greater than or equal to 1")
        return float(value)
