"""Application-boundary console and rotating-file logging setup."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TextIO

from .crawler_config import ConfigurationError, LoggingSettings


_APPLICATION_HANDLER = "_web_crawler_application_handler"
_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def configure_logging(
    settings: LoggingSettings,
    *,
    stream: TextIO | None = None,
) -> tuple[logging.Handler, ...]:
    """Replace only application-owned root handlers with validated settings."""
    if not isinstance(settings, LoggingSettings):
        raise ConfigurationError("settings must be LoggingSettings")

    level_name = _normalize_level(settings.level)
    level = _LEVELS[level_name]
    max_bytes = _positive_int(settings.max_bytes, "logging.max_bytes")
    backup_count = _non_negative_int(
        settings.backup_count,
        "logging.backup_count",
    )
    log_path = _optional_path(settings.file)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    new_handlers: list[logging.Handler] = []
    console = logging.StreamHandler(sys.stderr if stream is None else stream)
    _prepare_handler(console, level, formatter)
    new_handlers.append(console)

    try:
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            _prepare_handler(file_handler, level, formatter)
            new_handlers.append(file_handler)
    except (OSError, ValueError) as error:
        for handler in new_handlers:
            handler.close()
        raise ConfigurationError(
            f"cannot configure logging file {log_path}: {error}"
        ) from error

    root = logging.getLogger()
    previous_handlers = [
        handler
        for handler in root.handlers
        if getattr(handler, _APPLICATION_HANDLER, False)
    ]
    for handler in previous_handlers:
        root.removeHandler(handler)
        handler.close()
    for handler in new_handlers:
        root.addHandler(handler)
    root.setLevel(level)
    return tuple(new_handlers)


def _prepare_handler(
    handler: logging.Handler,
    level: int,
    formatter: logging.Formatter,
) -> None:
    handler.setLevel(level)
    handler.setFormatter(formatter)
    setattr(handler, _APPLICATION_HANDLER, True)


def _normalize_level(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("logging.level must be a non-empty string")
    normalized = value.strip().upper()
    if normalized not in _LEVELS:
        raise ConfigurationError(
            "logging.level must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL"
        )
    return normalized


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


def _optional_path(value: object) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, Path):
        raise ConfigurationError("logging.file must be a Path or None")
    return value
