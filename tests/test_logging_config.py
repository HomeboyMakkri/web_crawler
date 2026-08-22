import importlib
import io
import logging
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from src import logging_config
from src.crawler_config import ConfigurationError, LoggingSettings
from src.logging_config import configure_logging


def application_handlers(root: logging.Logger) -> list[logging.Handler]:
    return [
        handler
        for handler in root.handlers
        if getattr(handler, "_web_crawler_application_handler", False)
    ]


@pytest.fixture
def isolated_root_logger() -> Iterator[logging.Logger]:
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    for handler in original_handlers:
        root.removeHandler(handler)
    try:
        yield root
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in original_handlers:
            root.addHandler(handler)
        root.setLevel(original_level)


@pytest.mark.parametrize(
    ("name", "level"),
    [
        ("debug", logging.DEBUG),
        ("INFO", logging.INFO),
        ("Warning", logging.WARNING),
        ("ERROR", logging.ERROR),
        ("critical", logging.CRITICAL),
    ],
)
def test_supported_levels_are_case_insensitive(
    isolated_root_logger: logging.Logger,
    name: str,
    level: int,
) -> None:
    handlers = configure_logging(LoggingSettings(level=name), stream=io.StringIO())

    assert isolated_root_logger.level == level
    assert len(handlers) == 1
    assert handlers[0].level == level


@pytest.mark.parametrize("invalid", ["TRACE", "", "  ", 10, None])
def test_invalid_level_is_rejected_without_mutating_root(
    isolated_root_logger: logging.Logger,
    invalid: object,
) -> None:
    unrelated = logging.NullHandler()
    isolated_root_logger.addHandler(unrelated)

    with pytest.raises(ConfigurationError, match="logging.level"):
        configure_logging(
            LoggingSettings(level=invalid),  # type: ignore[arg-type]
            stream=io.StringIO(),
        )

    assert unrelated in isolated_root_logger.handlers
    assert application_handlers(isolated_root_logger) == []


def test_console_only_logging_uses_expected_format_and_level(
    isolated_root_logger: logging.Logger,
) -> None:
    output = io.StringIO()
    handlers = configure_logging(LoggingSettings(level="INFO"), stream=output)

    logging.getLogger("crawler.test").info("готово")

    assert len(handlers) == 1
    assert type(handlers[0]) is logging.StreamHandler
    rendered = output.getvalue()
    assert "INFO | crawler.test | готово" in rendered


def test_file_logging_is_utf8_and_uses_rotation_settings(
    isolated_root_logger: logging.Logger,
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "crawler.log"
    handlers = configure_logging(
        LoggingSettings(
            level="DEBUG",
            file=path,
            max_bytes=256,
            backup_count=2,
        ),
        stream=io.StringIO(),
    )

    assert path.parent.is_dir()
    assert len(handlers) == 2
    file_handler = handlers[1]
    assert isinstance(file_handler, RotatingFileHandler)
    assert file_handler.encoding == "utf-8"
    assert file_handler.maxBytes == 256
    assert file_handler.backupCount == 2

    logging.getLogger("crawler.unicode").warning("Привет, мир")
    file_handler.flush()
    assert "Привет, мир" in path.read_text(encoding="utf-8")


def test_file_handler_actually_rotates(
    isolated_root_logger: logging.Logger,
    tmp_path: Path,
) -> None:
    path = tmp_path / "crawler.log"
    handlers = configure_logging(
        LoggingSettings(file=path, max_bytes=100, backup_count=2),
        stream=io.StringIO(),
    )
    file_handler = handlers[1]
    assert isinstance(file_handler, RotatingFileHandler)

    logger = logging.getLogger("crawler.rotation")
    for index in range(20):
        logger.info("record-%02d-%s", index, "x" * 40)
    file_handler.flush()

    assert path.exists()
    assert (tmp_path / "crawler.log.1").exists()


def test_repeated_configuration_replaces_owned_handlers_without_duplicates(
    isolated_root_logger: logging.Logger,
) -> None:
    first_output = io.StringIO()
    second_output = io.StringIO()
    first = configure_logging(LoggingSettings(), stream=first_output)
    second = configure_logging(LoggingSettings(), stream=second_output)

    assert application_handlers(isolated_root_logger) == list(second)
    assert not any(handler in isolated_root_logger.handlers for handler in first)
    logging.getLogger("crawler.once").info("once")
    assert first_output.getvalue() == ""
    assert second_output.getvalue().splitlines() == [
        second_output.getvalue().strip()
    ]
    assert second_output.getvalue().rstrip().endswith("| once")


def test_unrelated_root_handlers_are_preserved(
    isolated_root_logger: logging.Logger,
) -> None:
    unrelated_stream = io.StringIO()
    unrelated = logging.StreamHandler(unrelated_stream)
    isolated_root_logger.addHandler(unrelated)

    first = configure_logging(LoggingSettings(), stream=io.StringIO())
    second = configure_logging(LoggingSettings(level="ERROR"), stream=io.StringIO())

    assert unrelated in isolated_root_logger.handlers
    assert first[0] not in isolated_root_logger.handlers
    assert second[0] in isolated_root_logger.handlers


def test_setup_failure_propagates_and_preserves_existing_handlers(
    isolated_root_logger: logging.Logger,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = configure_logging(LoggingSettings(), stream=io.StringIO())

    def fail_handler(*args: object, **kwargs: object) -> RotatingFileHandler:
        raise OSError("permission denied")

    monkeypatch.setattr(logging_config, "RotatingFileHandler", fail_handler)

    with pytest.raises(ConfigurationError, match="permission denied"):
        configure_logging(
            LoggingSettings(file=tmp_path / "crawler.log"),
            stream=io.StringIO(),
        )

    assert application_handlers(isolated_root_logger) == list(existing)


@pytest.mark.parametrize(
    "settings",
    [
        LoggingSettings(max_bytes=0),
        LoggingSettings(max_bytes=True),  # type: ignore[arg-type]
        LoggingSettings(backup_count=-1),
        LoggingSettings(backup_count=True),  # type: ignore[arg-type]
        LoggingSettings(file="crawler.log"),  # type: ignore[arg-type]
    ],
)
def test_direct_settings_are_revalidated_at_application_boundary(
    isolated_root_logger: logging.Logger,
    settings: LoggingSettings,
) -> None:
    with pytest.raises(ConfigurationError, match="logging"):
        configure_logging(settings, stream=io.StringIO())
    assert application_handlers(isolated_root_logger) == []


def test_module_import_has_no_logging_side_effect() -> None:
    root = logging.getLogger()
    before = list(root.handlers)

    importlib.reload(logging_config)

    assert root.handlers == before
