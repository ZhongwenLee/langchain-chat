from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from src.config_manager import AppConfig, ConfigBundle, EnvironmentConfig, LoggingConfig, SecretConfig
from src.models import Message, MessageRole, Session, User
from src.storage import (
    SQLiteStorageBackend,
    StorageBackendType,
    StorageFactory,
    StoragePagination,
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
    backend = StorageFactory().create(config_bundle)

    assert isinstance(backend, SQLiteStorageBackend)
    assert backend.backend_type is StorageBackendType.SQLITE
