from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from src.config_manager import AppConfig, ConfigBundle, EnvironmentConfig, LoggingConfig, SecretConfig
from src.models import Message, MessageRole, Session, User
from src.storage import (
    FileStorageBackend,
    MySQLStorageBackend,
    SQLiteStorageBackend,
    StorageBackendType,
    StorageFactory,
    StoragePagination,
    StorageSearchQuery,
)


@pytest.fixture()
def config_bundle(tmp_path: Path) -> ConfigBundle:
    return ConfigBundle(
        app=AppConfig(app_name="test-app", environment=EnvironmentConfig(name="test")),
        logging=LoggingConfig(version=1),
        secrets=SecretConfig(api_key="test-key", database_url=f"sqlite:///{tmp_path / 'app.db'}"),
    )


@pytest.mark.asyncio
async def test_sqlite_backend_persists_user_session_and_messages(config_bundle: ConfigBundle) -> None:
    # 这个测试覆盖默认后端最关键的业务闭环：先写入，再读回，并验证关系型对象可以跨连接持久化。
    backend = SQLiteStorageBackend(database_url=config_bundle.secrets.database_url, config=config_bundle)
    await backend.aconnect()

    user = User(id=uuid4(), username="alice", email="alice@example.com")
    session = Session(id=uuid4(), user_id=user.id, title="First chat")
    message = Message(id=uuid4(), session_id=session.id, role=MessageRole.USER, content="Hello", sequence=0)

    await backend.acreate("users", user)
    await backend.acreate("sessions", session)
    await backend.acreate("messages", message)

    stored_user = await backend.aget("users", str(user.id))
    stored_session = await backend.aget("sessions", str(session.id))
    stored_message = await backend.aget("messages", str(message.id))

    assert stored_user == user
    assert stored_session == session
    assert stored_message == message

    page = await backend.alist("messages", StoragePagination(page=1, page_size=10))
    assert page.total == 1
    assert page.items[0] == message

    await backend.aclose()


@pytest.mark.asyncio
async def test_sqlite_backend_reopens_existing_database(config_bundle: ConfigBundle) -> None:
    backend = SQLiteStorageBackend(database_url=config_bundle.secrets.database_url, config=config_bundle)
    await backend.aconnect()
    user = User(id=uuid4(), username="bob", email="bob@example.com")
    await backend.acreate("users", user)
    await backend.aclose()

    reopened = SQLiteStorageBackend(database_url=config_bundle.secrets.database_url, config=config_bundle)
    await reopened.aconnect()
    stored_user = await reopened.aget("users", str(user.id))

    assert stored_user == user
    await reopened.aclose()


def test_storage_factory_returns_sqlite_backend(config_bundle: ConfigBundle) -> None:
    backend = StorageFactory().create_backend(config_bundle)

    assert isinstance(backend, SQLiteStorageBackend)
    assert backend.backend_type is StorageBackendType.SQLITE


@pytest.mark.asyncio
async def test_file_backend_crud(config_bundle: ConfigBundle, tmp_path: Path) -> None:
    file_config = ConfigBundle(
        app=config_bundle.app,
        logging=config_bundle.logging,
        secrets=SecretConfig(api_key="test-key", database_url=f"file://{tmp_path / 'storage'}"),
    )
    backend = FileStorageBackend(base_path=tmp_path / "storage", config=file_config)
    backend.connect()

    user = User(id=uuid4(), username="carol", email="carol@example.com")
    await asyncio.to_thread(backend.create, "users", user)
    stored_user = await asyncio.to_thread(backend.get, "users", str(user.id))
    assert stored_user == user

    page = await asyncio.to_thread(backend.list, "users", StoragePagination(page=1, page_size=10))
    assert page.total == 1

    search = await asyncio.to_thread(backend.search, "users", StorageSearchQuery(keyword="carol", fields=("username",)))
    assert search.total == 1

    assert await asyncio.to_thread(backend.delete, "users", str(user.id)) is True
    assert await asyncio.to_thread(backend.get, "users", str(user.id)) is None


def test_factory_resolves_mysql_and_file(config_bundle: ConfigBundle, tmp_path: Path) -> None:
    mysql_bundle = ConfigBundle(app=config_bundle.app, logging=config_bundle.logging, secrets=SecretConfig(api_key="test-key", database_url="mysql://localhost/chat"))
    file_bundle = ConfigBundle(app=config_bundle.app, logging=config_bundle.logging, secrets=SecretConfig(api_key="test-key", database_url=f"file://{tmp_path / 'file-store'}"))

    mysql_backend = StorageFactory().create(mysql_bundle)
    file_backend = StorageFactory().create(file_bundle)

    assert isinstance(mysql_backend, MySQLStorageBackend)
    assert mysql_backend.backend_type is StorageBackendType.MYSQL
    assert isinstance(file_backend, FileStorageBackend)
    assert file_backend.backend_type is StorageBackendType.FILE
