import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.crawler_config import (
    CSVStorageConfig,
    ConfigurationError,
    CrawlerConfig,
    JSONLStorageConfig,
    SQLiteStorageConfig,
)


def write_config(path: Path, payload: object) -> Path:
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_empty_object_uses_complete_canonical_defaults(tmp_path: Path) -> None:
    config = CrawlerConfig.from_json(write_config(tmp_path / "config.json", {}))

    assert config.to_dict() == {
        "start_urls": [],
        "sitemap_urls": [],
        "crawl": {
            "max_concurrent": 10,
            "limit_per_host": None,
            "max_pages": 100,
            "max_depth": 2,
            "same_domain_only": True,
            "filter_external_links": False,
            "include_patterns": [],
            "exclude_patterns": [],
            "connect_timeout": 5.0,
            "read_timeout": 15.0,
            "total_timeout": 30.0,
            "timeout_multiplier": 2.0,
            "max_timeout": 120.0,
            "requests_per_second": None,
            "respect_robots": False,
            "min_delay": 0.0,
            "jitter": 0.0,
            "user_agent": "AsyncCrawler/1.0",
            "max_attempts": 4,
            "retry_base_delay": 0.5,
            "retry_max_delay": 30.0,
        },
        "storage": {"backends": []},
        "reporting": {
            "show_progress": False,
            "progress_interval": 1.0,
            "json_report": None,
            "html_report": None,
        },
        "logging": {
            "level": "INFO",
            "file": None,
            "max_bytes": 10_485_760,
            "backup_count": 3,
        },
    }


def test_partial_overrides_preserve_other_defaults_and_normalize_values() -> None:
    config = CrawlerConfig.from_dict(
        {
            "start_urls": ["  https://example.com/путь  "],
            "crawl": {
                "max_concurrent": 3,
                "max_depth": 0,
                "requests_per_second": 2,
                "min_delay": 0,
                "include_patterns": [r"/docs/\d+"],
            },
            "reporting": {"show_progress": True},
            "logging": {"level": "debug", "backup_count": 0},
        }
    )

    assert config.start_urls == ("https://example.com/путь",)
    assert config.crawl.max_concurrent == 3
    assert config.crawl.max_pages == 100
    assert config.crawl.max_depth == 0
    assert config.crawl.requests_per_second == 2.0
    assert config.crawl.min_delay == 0.0
    assert config.crawl.include_patterns == (r"/docs/\d+",)
    assert config.reporting.show_progress is True
    assert config.logging.level == "DEBUG"
    assert config.logging.backup_count == 0


def test_all_storage_types_and_relative_paths_resolve_from_config_file(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "nested"
    config_dir.mkdir()
    absolute_report = tmp_path / "absolute" / "report.json"
    config_path = write_config(
        config_dir / "config.json",
        {
            "storage": {
                "backends": [
                    {"type": "jsonl", "path": "data/pages.jsonl"},
                    {
                        "type": "csv",
                        "path": "data/pages.csv",
                        "encoding": "utf-16",
                    },
                    {
                        "type": "sqlite",
                        "path": "data/pages.db",
                        "batch_size": 25,
                    },
                ]
            },
            "reporting": {
                "json_report": str(absolute_report),
                "html_report": "reports/report.html",
            },
            "logging": {"file": "logs/crawler.log"},
        },
    )

    config = CrawlerConfig.from_json(config_path)
    jsonl, csv, sqlite = config.storage.backends

    assert jsonl == JSONLStorageConfig(
        path=(config_dir / "data/pages.jsonl").resolve()
    )
    assert csv == CSVStorageConfig(
        path=(config_dir / "data/pages.csv").resolve(),
        encoding="utf-16",
    )
    assert sqlite == SQLiteStorageConfig(
        path=(config_dir / "data/pages.db").resolve(),
        batch_size=25,
    )
    assert config.reporting.json_report == absolute_report.resolve()
    assert config.reporting.html_report == (
        config_dir / "reports/report.html"
    ).resolve()
    assert config.logging.file == (config_dir / "logs/crawler.log").resolve()
    assert not (config_dir / "data").exists()
    assert not (config_dir / "reports").exists()
    assert not (config_dir / "logs").exists()


def test_from_dict_resolves_paths_from_explicit_base_directory(
    tmp_path: Path,
) -> None:
    config = CrawlerConfig.from_dict(
        {
            "storage": {
                "backends": [{"type": "jsonl", "path": "pages.jsonl"}]
            }
        },
        base_dir=tmp_path,
    )

    backend = config.storage.backends[0]
    assert backend.path == (tmp_path / "pages.jsonl").resolve()


def test_model_is_frozen_and_uses_immutable_nested_collections() -> None:
    config = CrawlerConfig.from_dict(
        {
            "start_urls": ["https://example.com"],
            "crawl": {"include_patterns": ["example"]},
            "storage": {
                "backends": [{"type": "jsonl", "path": "pages.jsonl"}]
            },
        }
    )

    assert isinstance(config.start_urls, tuple)
    assert isinstance(config.crawl.include_patterns, tuple)
    assert isinstance(config.storage.backends, tuple)
    with pytest.raises(FrozenInstanceError):
        config.start_urls = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        config.crawl.max_pages = 1  # type: ignore[misc]


def test_to_dict_is_json_friendly_and_detached(tmp_path: Path) -> None:
    config = CrawlerConfig.from_dict(
        {
            "start_urls": ["https://example.com"],
            "crawl": {"include_patterns": ["docs"]},
            "storage": {
                "backends": [{"type": "jsonl", "path": "pages.jsonl"}]
            },
        },
        base_dir=tmp_path,
    )

    first = config.to_dict()
    json.dumps(first)
    start_urls = first["start_urls"]
    crawl = first["crawl"]
    storage = first["storage"]
    assert isinstance(start_urls, list)
    assert isinstance(crawl, dict)
    assert isinstance(storage, dict)
    start_urls.append("https://changed.example")
    include_patterns = crawl["include_patterns"]
    backends = storage["backends"]
    assert isinstance(include_patterns, list)
    assert isinstance(backends, list)
    include_patterns.append("changed")
    backends.clear()

    fresh = config.to_dict()
    assert fresh["start_urls"] == ["https://example.com"]
    fresh_crawl = fresh["crawl"]
    fresh_storage = fresh["storage"]
    assert isinstance(fresh_crawl, dict)
    assert fresh_crawl["include_patterns"] == ["docs"]
    assert isinstance(fresh_storage, dict)
    fresh_backends = fresh_storage["backends"]
    assert isinstance(fresh_backends, list)
    assert len(fresh_backends) == 1


def test_from_dict_does_not_mutate_caller_data(tmp_path: Path) -> None:
    raw = {
        "start_urls": ["https://example.com"],
        "crawl": {"include_patterns": ["docs"]},
        "reporting": {"json_report": "report.json"},
    }
    expected = json.loads(json.dumps(raw))

    CrawlerConfig.from_dict(raw, base_dir=tmp_path)

    assert raw == expected


def test_empty_sources_are_valid_until_effective_validation() -> None:
    config = CrawlerConfig.from_dict({})

    with pytest.raises(ConfigurationError, match="start_urls.*sitemap_urls"):
        config.validate_effective_sources()


@pytest.mark.parametrize(
    "sources",
    [
        {"start_urls": ["https://example.com"]},
        {"sitemap_urls": ["https://example.com/sitemap.xml"]},
    ],
)
def test_final_effective_source_validation_accepts_either_source(
    sources: dict[str, object],
) -> None:
    CrawlerConfig.from_dict(sources).validate_effective_sources()


def test_duplicate_sources_and_backend_declarations_preserve_input_order(
    tmp_path: Path,
) -> None:
    url = "https://example.com"
    config = CrawlerConfig.from_dict(
        {
            "start_urls": [url, url],
            "storage": {
                "backends": [
                    {"type": "jsonl", "path": "first.jsonl"},
                    {"type": "jsonl", "path": "second.jsonl"},
                ]
            },
        },
        base_dir=tmp_path,
    )

    assert config.start_urls == (url, url)
    assert [backend.path.name for backend in config.storage.backends] == [
        "first.jsonl",
        "second.jsonl",
    ]


def test_utf8_file_is_loaded_without_ascii_coercion(tmp_path: Path) -> None:
    path = write_config(
        tmp_path / "конфигурация.json",
        {
            "start_urls": ["https://example.com/страница"],
            "crawl": {"user_agent": "Учебный краулер/1.0"},
        },
    )

    config = CrawlerConfig.from_json(path)

    assert config.start_urls == ("https://example.com/страница",)
    assert config.crawl.user_agent == "Учебный краулер/1.0"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "configuration must be an object"),
        ({"unknown": 1}, "configuration.*unknown"),
        ({"crawl": {"unknown": 1}}, "crawl.*unknown"),
        ({"storage": {"unknown": 1}}, "storage.*unknown"),
        ({"reporting": {"unknown": 1}}, "reporting.*unknown"),
        ({"logging": {"unknown": 1}}, "logging.*unknown"),
        (
            {
                "storage": {
                    "backends": [
                        {"type": "jsonl", "path": "pages", "unknown": 1}
                    ]
                }
            },
            r"storage\.backends\[0\].*unknown",
        ),
    ],
)
def test_root_and_nested_unknown_keys_are_rejected(
    payload: object,
    message: str,
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        CrawlerConfig.from_dict(payload)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"start_urls": "https://example.com"}, "start_urls must be an array"),
        ({"sitemap_urls": [""]}, r"sitemap_urls\[0\]"),
        ({"start_urls": ["ftp://example.com"]}, r"start_urls\[0\].*HTTP"),
        ({"start_urls": ["/relative"]}, r"start_urls\[0\].*HTTP"),
        ({"crawl": []}, "crawl must be an object"),
        ({"storage": []}, "storage must be an object"),
        ({"storage": {"backends": {}}}, "backends must be an array"),
        ({"reporting": []}, "reporting must be an object"),
        ({"logging": []}, "logging must be an object"),
    ],
)
def test_invalid_container_and_source_values_are_rejected(
    payload: object,
    message: str,
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        CrawlerConfig.from_dict(payload)


@pytest.mark.parametrize(
    "field_name",
    ["max_concurrent", "max_pages", "max_attempts"],
)
@pytest.mark.parametrize("invalid", [True, 0, -1, 1.5, "1", None])
def test_positive_crawl_integer_fields_reject_invalid_values(
    field_name: str,
    invalid: object,
) -> None:
    with pytest.raises(ConfigurationError, match=rf"crawl\.{field_name}"):
        CrawlerConfig.from_dict({"crawl": {field_name: invalid}})


@pytest.mark.parametrize("invalid", [True, 0, -1, 1.5, "1"])
def test_optional_limit_per_host_requires_null_or_positive_integer(
    invalid: object,
) -> None:
    with pytest.raises(ConfigurationError, match="crawl.limit_per_host"):
        CrawlerConfig.from_dict({"crawl": {"limit_per_host": invalid}})


@pytest.mark.parametrize("field_name", ["max_depth"])
@pytest.mark.parametrize("invalid", [True, -1, 1.5, "0", None])
def test_non_negative_crawl_integer_fields_reject_invalid_values(
    field_name: str,
    invalid: object,
) -> None:
    with pytest.raises(ConfigurationError, match=rf"crawl\.{field_name}"):
        CrawlerConfig.from_dict({"crawl": {field_name: invalid}})


@pytest.mark.parametrize(
    "field_name",
    [
        "connect_timeout",
        "read_timeout",
        "total_timeout",
        "max_timeout",
        "retry_base_delay",
        "retry_max_delay",
    ],
)
@pytest.mark.parametrize(
    "invalid",
    [True, 0, -1, "1", None, float("nan"), float("inf")],
)
def test_positive_crawl_numbers_reject_invalid_values(
    field_name: str,
    invalid: object,
) -> None:
    with pytest.raises(ConfigurationError, match=rf"crawl\.{field_name}"):
        CrawlerConfig.from_dict({"crawl": {field_name: invalid}})


@pytest.mark.parametrize(
    "invalid",
    [True, 0, -1, "1", float("nan"), float("inf")],
)
def test_optional_request_rate_requires_null_or_positive_finite_number(
    invalid: object,
) -> None:
    with pytest.raises(ConfigurationError, match="crawl.requests_per_second"):
        CrawlerConfig.from_dict({"crawl": {"requests_per_second": invalid}})


@pytest.mark.parametrize("field_name", ["min_delay", "jitter"])
@pytest.mark.parametrize(
    "invalid",
    [True, -1, "0", None, float("nan"), float("inf")],
)
def test_non_negative_crawl_numbers_reject_invalid_values(
    field_name: str,
    invalid: object,
) -> None:
    with pytest.raises(ConfigurationError, match=rf"crawl\.{field_name}"):
        CrawlerConfig.from_dict({"crawl": {field_name: invalid}})


@pytest.mark.parametrize(
    "field_name",
    ["same_domain_only", "filter_external_links", "respect_robots"],
)
@pytest.mark.parametrize("invalid", [0, 1, "true", None])
def test_crawl_boolean_fields_require_actual_booleans(
    field_name: str,
    invalid: object,
) -> None:
    with pytest.raises(ConfigurationError, match=rf"crawl\.{field_name}"):
        CrawlerConfig.from_dict({"crawl": {field_name: invalid}})


def test_timeout_and_retry_cross_field_boundaries() -> None:
    config = CrawlerConfig.from_dict(
        {
            "crawl": {
                "connect_timeout": 5,
                "read_timeout": 5,
                "total_timeout": 5,
                "max_timeout": 5,
                "timeout_multiplier": 1,
                "retry_base_delay": 2,
                "retry_max_delay": 2,
            }
        }
    )
    assert config.crawl.max_timeout == 5.0
    assert config.crawl.timeout_multiplier == 1.0
    assert config.crawl.retry_max_delay == 2.0

    with pytest.raises(ConfigurationError, match="timeout_multiplier"):
        CrawlerConfig.from_dict({"crawl": {"timeout_multiplier": 0.5}})
    with pytest.raises(ConfigurationError, match="max_timeout"):
        CrawlerConfig.from_dict({"crawl": {"max_timeout": 29}})
    with pytest.raises(ConfigurationError, match="retry_max_delay"):
        CrawlerConfig.from_dict(
            {"crawl": {"retry_base_delay": 2, "retry_max_delay": 1}}
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"crawl": {"include_patterns": "docs"}}, "include_patterns.*array"),
        ({"crawl": {"exclude_patterns": [""]}}, r"exclude_patterns\[0\]"),
        ({"crawl": {"include_patterns": ["["]}}, "regular expression"),
        ({"crawl": {"user_agent": "  "}}, "user_agent"),
    ],
)
def test_pattern_and_string_validation(payload: object, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        CrawlerConfig.from_dict(payload)


@pytest.mark.parametrize(
    ("backend", "message"),
    [
        ([], r"backends\[0\].*object"),
        ({"path": "pages"}, r"backends\[0\]\.type"),
        ({"type": "xml", "path": "pages"}, r"backends\[0\]\.type"),
        ({"type": "jsonl", "path": ""}, r"backends\[0\]\.path"),
        (
            {"type": "jsonl", "path": "pages", "encoding": "utf-8"},
            r"backends\[0\].*encoding",
        ),
        (
            {"type": "csv", "path": "pages", "batch_size": 10},
            r"backends\[0\].*batch_size",
        ),
        (
            {"type": "sqlite", "path": "pages", "encoding": "utf-8"},
            r"backends\[0\].*encoding",
        ),
        (
            {"type": "csv", "path": "pages", "encoding": ""},
            r"backends\[0\]\.encoding",
        ),
        (
            {"type": "sqlite", "path": "pages", "batch_size": True},
            r"backends\[0\]\.batch_size",
        ),
        (
            {"type": "sqlite", "path": "pages", "batch_size": 0},
            r"backends\[0\]\.batch_size",
        ),
    ],
)
def test_storage_backend_validation(backend: object, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        CrawlerConfig.from_dict({"storage": {"backends": [backend]}})


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"reporting": {"show_progress": 1}}, "show_progress"),
        ({"reporting": {"progress_interval": 0}}, "progress_interval"),
        ({"reporting": {"progress_interval": float("nan")}}, "progress_interval"),
        ({"reporting": {"json_report": ""}}, "json_report"),
        ({"logging": {"level": "TRACE"}}, "logging.level"),
        ({"logging": {"file": ""}}, "logging.file"),
        ({"logging": {"max_bytes": True}}, "logging.max_bytes"),
        ({"logging": {"max_bytes": 0}}, "logging.max_bytes"),
        ({"logging": {"backup_count": True}}, "logging.backup_count"),
        ({"logging": {"backup_count": -1}}, "logging.backup_count"),
    ],
)
def test_reporting_and_logging_validation(
    payload: object,
    message: str,
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        CrawlerConfig.from_dict(payload)


def test_missing_invalid_and_non_object_json_files(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        CrawlerConfig.from_json(tmp_path / "missing.json")

    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"crawl":', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="invalid JSON.*invalid.json"):
        CrawlerConfig.from_json(invalid)

    root_list = tmp_path / "list.json"
    root_list.write_text("[]", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="configuration must be an object"):
        CrawlerConfig.from_json(root_list)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_json_constants_are_rejected(
    tmp_path: Path,
    constant: str,
) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        f'{{"crawl": {{"connect_timeout": {constant}}}}}',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="non-finite JSON number"):
        CrawlerConfig.from_json(path)


@pytest.mark.parametrize("invalid", [None, True, 1, ""])
def test_configuration_path_validation(invalid: object) -> None:
    with pytest.raises(ConfigurationError, match="configuration path"):
        CrawlerConfig.from_json(invalid)  # type: ignore[arg-type]


def test_non_string_mapping_key_is_rejected_actionably() -> None:
    with pytest.raises(ConfigurationError, match="configuration.*string keys"):
        CrawlerConfig.from_dict({1: "value"})
