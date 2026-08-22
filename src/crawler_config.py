"""Strict immutable JSON configuration for the Day 7 crawler facade."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from numbers import Real
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """A configuration file or field violates the public contract."""


@dataclass(frozen=True, slots=True)
class CrawlSettings:
    max_concurrent: int = 10
    limit_per_host: int | None = None
    max_pages: int = 100
    max_depth: int = 2
    same_domain_only: bool = True
    filter_external_links: bool = False
    include_patterns: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()
    connect_timeout: float = 5.0
    read_timeout: float = 15.0
    total_timeout: float = 30.0
    timeout_multiplier: float = 2.0
    max_timeout: float = 120.0
    requests_per_second: float | None = None
    respect_robots: bool = False
    min_delay: float = 0.0
    jitter: float = 0.0
    user_agent: str = "AsyncCrawler/1.0"
    max_attempts: int = 4
    retry_base_delay: float = 0.5
    retry_max_delay: float = 30.0


@dataclass(frozen=True, slots=True)
class JSONLStorageConfig:
    path: Path
    type: Literal["jsonl"] = field(default="jsonl", init=False)


@dataclass(frozen=True, slots=True)
class CSVStorageConfig:
    path: Path
    encoding: str = "utf-8"
    type: Literal["csv"] = field(default="csv", init=False)


@dataclass(frozen=True, slots=True)
class SQLiteStorageConfig:
    path: Path
    batch_size: int = 100
    type: Literal["sqlite"] = field(default="sqlite", init=False)


StorageBackendConfig: TypeAlias = (
    JSONLStorageConfig | CSVStorageConfig | SQLiteStorageConfig
)


@dataclass(frozen=True, slots=True)
class StorageSettings:
    backends: tuple[StorageBackendConfig, ...] = ()


@dataclass(frozen=True, slots=True)
class ReportingSettings:
    show_progress: bool = False
    progress_interval: float = 1.0
    json_report: Path | None = None
    html_report: Path | None = None


@dataclass(frozen=True, slots=True)
class LoggingSettings:
    level: str = "INFO"
    file: Path | None = None
    max_bytes: int = 10_485_760
    backup_count: int = 3


@dataclass(frozen=True, slots=True)
class CrawlerConfig:
    """Detached, immutable effective configuration values."""

    start_urls: tuple[str, ...] = ()
    sitemap_urls: tuple[str, ...] = ()
    crawl: CrawlSettings = field(default_factory=CrawlSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    reporting: ReportingSettings = field(default_factory=ReportingSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)

    @classmethod
    def from_json(cls, path: str | Path) -> CrawlerConfig:
        """Load strict UTF-8 JSON and resolve its relative output paths."""
        config_path = _normalize_config_path(path)
        try:
            payload = config_path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise ConfigurationError(
                f"configuration file not found: {config_path}"
            ) from error
        except (OSError, UnicodeError) as error:
            raise ConfigurationError(
                f"cannot read configuration file {config_path}: {error}"
            ) from error

        try:
            raw = json.loads(payload, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, ValueError) as error:
            raise ConfigurationError(
                f"invalid JSON in configuration file {config_path}: {error}"
            ) from error

        return cls.from_dict(raw, base_dir=config_path.parent)

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        base_dir: str | Path | None = None,
    ) -> CrawlerConfig:
        """Parse an already-decoded JSON value without retaining caller state."""
        root = _expect_object(value, "configuration")
        _reject_unknown(
            root,
            {
                "start_urls",
                "sitemap_urls",
                "crawl",
                "storage",
                "reporting",
                "logging",
            },
            "configuration",
        )
        resolved_base = _normalize_base_dir(base_dir)

        return cls(
            start_urls=_parse_url_list(
                root.get("start_urls", []),
                "start_urls",
            ),
            sitemap_urls=_parse_url_list(
                root.get("sitemap_urls", []),
                "sitemap_urls",
            ),
            crawl=_parse_crawl(root.get("crawl", {})),
            storage=_parse_storage(root.get("storage", {}), resolved_base),
            reporting=_parse_reporting(
                root.get("reporting", {}),
                resolved_base,
            ),
            logging=_parse_logging(root.get("logging", {}), resolved_base),
        )

    def validate_effective_sources(self) -> None:
        """Reject a final merged configuration that has no crawl source."""
        if not self.start_urls and not self.sitemap_urls:
            raise ConfigurationError(
                "effective configuration requires at least one start_urls "
                "or sitemap_urls entry"
            )

    def to_dict(self) -> dict[str, object]:
        """Return a detached JSON-friendly canonical configuration snapshot."""
        return {
            "start_urls": list(self.start_urls),
            "sitemap_urls": list(self.sitemap_urls),
            "crawl": {
                "max_concurrent": self.crawl.max_concurrent,
                "limit_per_host": self.crawl.limit_per_host,
                "max_pages": self.crawl.max_pages,
                "max_depth": self.crawl.max_depth,
                "same_domain_only": self.crawl.same_domain_only,
                "filter_external_links": self.crawl.filter_external_links,
                "include_patterns": list(self.crawl.include_patterns),
                "exclude_patterns": list(self.crawl.exclude_patterns),
                "connect_timeout": self.crawl.connect_timeout,
                "read_timeout": self.crawl.read_timeout,
                "total_timeout": self.crawl.total_timeout,
                "timeout_multiplier": self.crawl.timeout_multiplier,
                "max_timeout": self.crawl.max_timeout,
                "requests_per_second": self.crawl.requests_per_second,
                "respect_robots": self.crawl.respect_robots,
                "min_delay": self.crawl.min_delay,
                "jitter": self.crawl.jitter,
                "user_agent": self.crawl.user_agent,
                "max_attempts": self.crawl.max_attempts,
                "retry_base_delay": self.crawl.retry_base_delay,
                "retry_max_delay": self.crawl.retry_max_delay,
            },
            "storage": {
                "backends": [
                    _serialize_backend(backend)
                    for backend in self.storage.backends
                ]
            },
            "reporting": {
                "show_progress": self.reporting.show_progress,
                "progress_interval": self.reporting.progress_interval,
                "json_report": _serialize_path(self.reporting.json_report),
                "html_report": _serialize_path(self.reporting.html_report),
            },
            "logging": {
                "level": self.logging.level,
                "file": _serialize_path(self.logging.file),
                "max_bytes": self.logging.max_bytes,
                "backup_count": self.logging.backup_count,
            },
        }


_CRAWL_KEYS = {
    "max_concurrent",
    "limit_per_host",
    "max_pages",
    "max_depth",
    "same_domain_only",
    "filter_external_links",
    "include_patterns",
    "exclude_patterns",
    "connect_timeout",
    "read_timeout",
    "total_timeout",
    "timeout_multiplier",
    "max_timeout",
    "requests_per_second",
    "respect_robots",
    "min_delay",
    "jitter",
    "user_agent",
    "max_attempts",
    "retry_base_delay",
    "retry_max_delay",
}


def _parse_crawl(value: object) -> CrawlSettings:
    data = _expect_object(value, "crawl")
    _reject_unknown(data, _CRAWL_KEYS, "crawl")
    defaults = CrawlSettings()

    connect_timeout = _positive_number(
        data.get("connect_timeout", defaults.connect_timeout),
        "crawl.connect_timeout",
    )
    read_timeout = _positive_number(
        data.get("read_timeout", defaults.read_timeout),
        "crawl.read_timeout",
    )
    total_timeout = _positive_number(
        data.get("total_timeout", defaults.total_timeout),
        "crawl.total_timeout",
    )
    timeout_multiplier = _positive_number(
        data.get("timeout_multiplier", defaults.timeout_multiplier),
        "crawl.timeout_multiplier",
    )
    if timeout_multiplier < 1.0:
        raise ConfigurationError(
            "crawl.timeout_multiplier must be greater than or equal to 1"
        )
    max_timeout = _positive_number(
        data.get("max_timeout", defaults.max_timeout),
        "crawl.max_timeout",
    )
    if max_timeout < max(connect_timeout, read_timeout, total_timeout):
        raise ConfigurationError(
            "crawl.max_timeout must be greater than or equal to initial timeouts"
        )

    retry_base_delay = _positive_number(
        data.get("retry_base_delay", defaults.retry_base_delay),
        "crawl.retry_base_delay",
    )
    retry_max_delay = _positive_number(
        data.get("retry_max_delay", defaults.retry_max_delay),
        "crawl.retry_max_delay",
    )
    if retry_max_delay < retry_base_delay:
        raise ConfigurationError(
            "crawl.retry_max_delay must be greater than or equal to "
            "crawl.retry_base_delay"
        )

    return CrawlSettings(
        max_concurrent=_positive_int(
            data.get("max_concurrent", defaults.max_concurrent),
            "crawl.max_concurrent",
        ),
        limit_per_host=_optional_positive_int(
            data.get("limit_per_host", defaults.limit_per_host),
            "crawl.limit_per_host",
        ),
        max_pages=_positive_int(
            data.get("max_pages", defaults.max_pages),
            "crawl.max_pages",
        ),
        max_depth=_non_negative_int(
            data.get("max_depth", defaults.max_depth),
            "crawl.max_depth",
        ),
        same_domain_only=_boolean(
            data.get("same_domain_only", defaults.same_domain_only),
            "crawl.same_domain_only",
        ),
        filter_external_links=_boolean(
            data.get(
                "filter_external_links",
                defaults.filter_external_links,
            ),
            "crawl.filter_external_links",
        ),
        include_patterns=_patterns(
            data.get("include_patterns", defaults.include_patterns),
            "crawl.include_patterns",
        ),
        exclude_patterns=_patterns(
            data.get("exclude_patterns", defaults.exclude_patterns),
            "crawl.exclude_patterns",
        ),
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        total_timeout=total_timeout,
        timeout_multiplier=timeout_multiplier,
        max_timeout=max_timeout,
        requests_per_second=_optional_positive_number(
            data.get(
                "requests_per_second",
                defaults.requests_per_second,
            ),
            "crawl.requests_per_second",
        ),
        respect_robots=_boolean(
            data.get("respect_robots", defaults.respect_robots),
            "crawl.respect_robots",
        ),
        min_delay=_non_negative_number(
            data.get("min_delay", defaults.min_delay),
            "crawl.min_delay",
        ),
        jitter=_non_negative_number(
            data.get("jitter", defaults.jitter),
            "crawl.jitter",
        ),
        user_agent=_non_empty_string(
            data.get("user_agent", defaults.user_agent),
            "crawl.user_agent",
        ),
        max_attempts=_positive_int(
            data.get("max_attempts", defaults.max_attempts),
            "crawl.max_attempts",
        ),
        retry_base_delay=retry_base_delay,
        retry_max_delay=retry_max_delay,
    )


def _parse_storage(value: object, base_dir: Path) -> StorageSettings:
    data = _expect_object(value, "storage")
    _reject_unknown(data, {"backends"}, "storage")
    raw_backends = data.get("backends", [])
    if not isinstance(raw_backends, list):
        raise ConfigurationError("storage.backends must be an array")
    return StorageSettings(
        tuple(
            _parse_storage_backend(backend, index, base_dir)
            for index, backend in enumerate(raw_backends)
        )
    )


def _parse_storage_backend(
    value: object,
    index: int,
    base_dir: Path,
) -> StorageBackendConfig:
    field_name = f"storage.backends[{index}]"
    data = _expect_object(value, field_name)
    backend_type = _non_empty_string(data.get("type"), f"{field_name}.type")

    allowed_keys = {
        "jsonl": {"type", "path"},
        "csv": {"type", "path", "encoding"},
        "sqlite": {"type", "path", "batch_size"},
    }
    if backend_type not in allowed_keys:
        raise ConfigurationError(
            f"{field_name}.type must be one of: jsonl, csv, sqlite"
        )
    _reject_unknown(data, allowed_keys[backend_type], field_name)
    path = _resolved_path(data.get("path"), f"{field_name}.path", base_dir)

    if backend_type == "jsonl":
        return JSONLStorageConfig(path=path)
    if backend_type == "csv":
        encoding = _non_empty_string(
            data.get("encoding", "utf-8"),
            f"{field_name}.encoding",
        )
        return CSVStorageConfig(path=path, encoding=encoding)
    return SQLiteStorageConfig(
        path=path,
        batch_size=_positive_int(
            data.get("batch_size", 100),
            f"{field_name}.batch_size",
        ),
    )


def _parse_reporting(value: object, base_dir: Path) -> ReportingSettings:
    data = _expect_object(value, "reporting")
    _reject_unknown(
        data,
        {"show_progress", "progress_interval", "json_report", "html_report"},
        "reporting",
    )
    defaults = ReportingSettings()
    return ReportingSettings(
        show_progress=_boolean(
            data.get("show_progress", defaults.show_progress),
            "reporting.show_progress",
        ),
        progress_interval=_positive_number(
            data.get("progress_interval", defaults.progress_interval),
            "reporting.progress_interval",
        ),
        json_report=_optional_resolved_path(
            data.get("json_report", defaults.json_report),
            "reporting.json_report",
            base_dir,
        ),
        html_report=_optional_resolved_path(
            data.get("html_report", defaults.html_report),
            "reporting.html_report",
            base_dir,
        ),
    )


def _parse_logging(value: object, base_dir: Path) -> LoggingSettings:
    data = _expect_object(value, "logging")
    _reject_unknown(
        data,
        {"level", "file", "max_bytes", "backup_count"},
        "logging",
    )
    defaults = LoggingSettings()
    level = _non_empty_string(
        data.get("level", defaults.level),
        "logging.level",
    ).upper()
    supported_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if level not in supported_levels:
        raise ConfigurationError(
            "logging.level must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL"
        )
    return LoggingSettings(
        level=level,
        file=_optional_resolved_path(
            data.get("file", defaults.file),
            "logging.file",
            base_dir,
        ),
        max_bytes=_positive_int(
            data.get("max_bytes", defaults.max_bytes),
            "logging.max_bytes",
        ),
        backup_count=_non_negative_int(
            data.get("backup_count", defaults.backup_count),
            "logging.backup_count",
        ),
    )


def _expect_object(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{field_name} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ConfigurationError(f"{field_name} must use string keys")
    return cast(dict[str, object], value)


def _reject_unknown(
    value: Mapping[str, object],
    allowed: set[str],
    field_name: str,
) -> None:
    unknown = sorted(key for key in value if key not in allowed)
    if unknown:
        raise ConfigurationError(
            f"unknown key at {field_name}: {unknown[0]}"
        )


def _parse_url_list(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigurationError(f"{field_name} must be an array")
    urls: list[str] = []
    for index, raw_url in enumerate(value):
        item_name = f"{field_name}[{index}]"
        url = _non_empty_string(raw_url, item_name)
        try:
            parsed = urlsplit(url)
            parsed.port
        except ValueError as error:
            raise ConfigurationError(
                f"{item_name} must be an absolute HTTP or HTTPS URL"
            ) from error
        if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname is None:
            raise ConfigurationError(
                f"{item_name} must be an absolute HTTP or HTTPS URL"
            )
        urls.append(url)
    return tuple(urls)


def _patterns(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ConfigurationError(f"{field_name} must be an array")
    patterns: list[str] = []
    for index, raw_pattern in enumerate(value):
        item_name = f"{field_name}[{index}]"
        pattern = _non_empty_string(raw_pattern, item_name)
        try:
            re.compile(pattern)
        except re.error as error:
            raise ConfigurationError(
                f"{item_name} is not a valid regular expression: {error}"
            ) from error
        patterns.append(pattern)
    return tuple(patterns)


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigurationError(f"{field_name} must be a positive integer")
    return value


def _non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigurationError(
            f"{field_name} must be a non-negative integer"
        )
    return value


def _optional_positive_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, field_name)


def _positive_number(value: object, field_name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ConfigurationError(
            f"{field_name} must be a positive finite number"
        )
    return float(value)


def _non_negative_number(value: object, field_name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(value)
        or value < 0
    ):
        raise ConfigurationError(
            f"{field_name} must be a non-negative finite number"
        )
    return float(value)


def _optional_positive_number(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    return _positive_number(value, field_name)


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{field_name} must be a boolean")
    return value


def _non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _resolved_path(value: object, field_name: str, base_dir: Path) -> Path:
    raw_path = _non_empty_string(value, field_name)
    path = Path(raw_path)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve(strict=False)


def _optional_resolved_path(
    value: object,
    field_name: str,
    base_dir: Path,
) -> Path | None:
    if value is None:
        return None
    return _resolved_path(value, field_name, base_dir)


def _normalize_config_path(value: str | Path) -> Path:
    if isinstance(value, bool) or not isinstance(value, (str, Path)):
        raise ConfigurationError("configuration path must be a string or Path")
    if isinstance(value, str) and not value.strip():
        raise ConfigurationError("configuration path must be non-empty")
    return Path(value).resolve(strict=False)


def _normalize_base_dir(value: str | Path | None) -> Path:
    if value is None:
        return Path.cwd().resolve(strict=False)
    if isinstance(value, bool) or not isinstance(value, (str, Path)):
        raise ConfigurationError("base_dir must be a string or Path")
    if isinstance(value, str) and not value.strip():
        raise ConfigurationError("base_dir must be non-empty")
    return Path(value).resolve(strict=False)


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _serialize_path(value: Path | None) -> str | None:
    return None if value is None else str(value)


def _serialize_backend(backend: StorageBackendConfig) -> dict[str, object]:
    if isinstance(backend, JSONLStorageConfig):
        return {"type": backend.type, "path": str(backend.path)}
    if isinstance(backend, CSVStorageConfig):
        return {
            "type": backend.type,
            "path": str(backend.path),
            "encoding": backend.encoding,
        }
    return {
        "type": backend.type,
        "path": str(backend.path),
        "batch_size": backend.batch_size,
    }
