from pathlib import Path

import pytest

from src.composite_storage import CompositeStorage
from src.crawler_config import (
    CSVStorageConfig,
    ConfigurationError,
    CrawlerConfig,
    JSONLStorageConfig,
    SQLiteStorageConfig,
    StorageSettings,
)
from src.csv_storage import CSVStorage
from src.json_storage import JSONStorage
from src.sqlite_storage import SQLiteStorage
from src.storage_factory import build_storage


def test_empty_settings_disable_storage() -> None:
    assert build_storage(StorageSettings()) is None


def test_single_jsonl_backend_is_returned_directly(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "pages.jsonl"

    storage = build_storage(
        StorageSettings((JSONLStorageConfig(path=path),))
    )

    assert isinstance(storage, JSONStorage)
    assert storage.path == path
    assert path.parent.is_dir()
    assert storage._file is None


def test_single_csv_backend_applies_encoding_without_opening_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "csv" / "pages.csv"

    storage = build_storage(
        StorageSettings((CSVStorageConfig(path=path, encoding="utf-16"),))
    )

    assert isinstance(storage, CSVStorage)
    assert storage.path == path
    assert storage.encoding == "utf-16"
    assert path.parent.is_dir()
    assert storage._file is None


def test_single_sqlite_backend_applies_batch_size_without_opening_database(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sqlite" / "pages.db"

    storage = build_storage(
        StorageSettings((SQLiteStorageConfig(path=path, batch_size=25),))
    )

    assert isinstance(storage, SQLiteStorage)
    assert storage.path == path
    assert storage.batch_size == 25
    assert path.parent.is_dir()
    assert storage._connection is None
    assert not path.exists()


def test_multiple_backends_use_composite_and_preserve_order(
    tmp_path: Path,
) -> None:
    settings = StorageSettings(
        (
            JSONLStorageConfig(path=tmp_path / "pages.jsonl"),
            CSVStorageConfig(path=tmp_path / "pages.csv"),
            SQLiteStorageConfig(path=tmp_path / "pages.db"),
        )
    )

    storage = build_storage(settings)

    assert isinstance(storage, CompositeStorage)
    assert [type(child) for child in storage.storages] == [
        JSONStorage,
        CSVStorage,
        SQLiteStorage,
    ]


def test_duplicate_backend_types_with_distinct_paths_are_supported(
    tmp_path: Path,
) -> None:
    first = tmp_path / "one" / "pages.jsonl"
    second = tmp_path / "two" / "pages.jsonl"

    storage = build_storage(
        StorageSettings(
            (
                JSONLStorageConfig(path=first),
                JSONLStorageConfig(path=second),
            )
        )
    )

    assert isinstance(storage, CompositeStorage)
    assert [child.path for child in storage.storages] == [first, second]
    assert first.parent.is_dir()
    assert second.parent.is_dir()


def test_factory_uses_paths_already_resolved_by_configuration(
    tmp_path: Path,
) -> None:
    config = CrawlerConfig.from_dict(
        {
            "storage": {
                "backends": [{"type": "jsonl", "path": "data/pages.jsonl"}]
            }
        },
        base_dir=tmp_path,
    )

    storage = build_storage(config.storage)

    assert isinstance(storage, JSONStorage)
    assert storage.path == (tmp_path / "data/pages.jsonl").resolve()


def test_invalid_backend_input_is_rejected_before_factory() -> None:
    with pytest.raises(ConfigurationError, match="storage.backends.*type"):
        CrawlerConfig.from_dict(
            {
                "storage": {
                    "backends": [{"type": "xml", "path": "pages.xml"}]
                }
            }
        )


def test_factory_rejects_non_settings_input() -> None:
    with pytest.raises(ValueError, match="StorageSettings"):
        build_storage([])  # type: ignore[arg-type]
