from __future__ import annotations

from pathlib import Path

import pytest

from src.config_manager import AppConfig, ConfigBundle, EnvironmentConfig, LoggingConfig, SecretConfig
from src.storage import (
    FileStorageBackend,
    MySQLStorageBackend,
    SQLiteStorageBackend,
    StorageBackendType,
    StorageFactory,
)


@pytest.fixture()
def config_bundle(tmp_path: Path) -> ConfigBundle:
    return ConfigBundle(
        app=AppConfig(app_name="test-app", environment=EnvironmentConfig(name="test")),
        logging=LoggingConfig(version=1),
        secrets=SecretConfig(api_key="test-key", database_url=f"sqlite:///{tmp_path / 'app.db'}"),
    )


def test_storage_factory_returns_sqlite_backend(config_bundle: ConfigBundle) -> None:
    backend = StorageFactory().create(config_bundle)

    assert isinstance(backend, SQLiteStorageBackend)
    assert backend.backend_type is StorageBackendType.SQLITE


def test_storage_factory_returns_mysql_backend(config_bundle: ConfigBundle) -> None:
    mysql_config = ConfigBundle(
        app=config_bundle.app,
        logging=config_bundle.logging,
        secrets=SecretConfig(api_key="test-key", database_url="mysql+pymysql://user:pass@localhost/db"),
    )

    backend = StorageFactory().create(mysql_config)

    assert isinstance(backend, MySQLStorageBackend)
    assert backend.backend_type is StorageBackendType.MYSQL


def test_storage_factory_returns_file_backend(config_bundle: ConfigBundle) -> None:
    file_config = ConfigBundle(
        app=config_bundle.app,
        logging=config_bundle.logging,
        secrets=SecretConfig(api_key="test-key", database_url="file:///tmp/storage"),
    )

    backend = StorageFactory().create(file_config)

    assert isinstance(backend, FileStorageBackend)
    assert backend.backend_type is StorageBackendType.FILE
