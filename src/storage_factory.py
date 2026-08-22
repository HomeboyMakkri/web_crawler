"""Construct existing storage backends from validated crawler settings."""

from .composite_storage import CompositeStorage
from .crawler_config import (
    CSVStorageConfig,
    JSONLStorageConfig,
    SQLiteStorageConfig,
    StorageBackendConfig,
    StorageSettings,
)
from .csv_storage import CSVStorage
from .data_storage import DataStorage
from .json_storage import JSONStorage
from .sqlite_storage import SQLiteStorage


def build_storage(settings: StorageSettings) -> DataStorage | None:
    """Build zero, one, or composite storage without opening I/O resources."""
    if not isinstance(settings, StorageSettings):
        raise ValueError("settings must be StorageSettings")

    storages = tuple(_build_backend(backend) for backend in settings.backends)
    if not storages:
        return None
    if len(storages) == 1:
        return storages[0]
    return CompositeStorage(storages)


def _build_backend(config: StorageBackendConfig) -> DataStorage:
    config.path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(config, JSONLStorageConfig):
        return JSONStorage(config.path)
    if isinstance(config, CSVStorageConfig):
        return CSVStorage(config.path, encoding=config.encoding)
    if isinstance(config, SQLiteStorageConfig):
        return SQLiteStorage(config.path, batch_size=config.batch_size)
    raise ValueError("unsupported storage backend configuration")
