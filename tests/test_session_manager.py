from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from src.config_manager import AppConfig, ConfigBundle, EnvironmentConfig, LoggingConfig, SecretConfig
from src.models import MessageRole
from src.session_manager import SessionManager, SessionManagerError
from src.storage import SQLiteStorageBackend
from src.user_manager import UserManager


@pytest.fixture()
def sqlite_backend(tmp_path: Path) -> SQLiteStorageBackend:
    config = ConfigBundle(
        app=AppConfig(app_name="test-app", environment=EnvironmentConfig()),
        logging=LoggingConfig(version=1),
        secrets=SecretConfig(api_key="test-key", database_url=f"sqlite:///{tmp_path / 'sessions.db'}"),
    )
    backend = SQLiteStorageBackend(database_url=config.secrets.database_url, config=config)
    backend.connect()
    yield backend
    backend.close()


@pytest.fixture()
def managers(sqlite_backend: SQLiteStorageBackend) -> tuple[UserManager, SessionManager]:
    user_manager = UserManager(sqlite_backend)
    session_manager = SessionManager(sqlite_backend, user_manager)
    return user_manager, session_manager


def test_create_list_rename_and_delete_session(managers: tuple[UserManager, SessionManager]) -> None:
    user_manager, session_manager = managers
    user = user_manager.create_user("Alice", "alice@example.com")

    session = session_manager.create_session(user.id, title="项目讨论")
    listed = session_manager.list_sessions(user.id)

    assert listed[0].title == "项目讨论"
    assert listed[0].user_id == user.id

    renamed = session_manager.rename_session(session.id, "新标题", user.id)
    assert renamed.title == "新标题"

    assert session_manager.delete_session(session.id, user.id) is True
    assert session_manager.get_session(session.id) is None


def test_auto_save_message_and_auto_title_generation(managers: tuple[UserManager, SessionManager]) -> None:
    user_manager, session_manager = managers
    user = user_manager.create_user("Alice", "alice@example.com")
    session = session_manager.create_session(user.id)

    message = session_manager.add_message(session.id, MessageRole.USER, "请帮我总结一下这个方案的优缺点", user_id=user.id)
    stored_messages = session_manager.list_messages(session.id, user.id)
    stored_session = session_manager.get_session(session.id)

    assert message.sequence == 0
    assert stored_messages == [message]
    assert stored_session is not None
    assert stored_session.title != "新对话"
    assert stored_session.updated_at >= stored_session.created_at


def test_load_history_session_sets_active_session(managers: tuple[UserManager, SessionManager]) -> None:
    user_manager, session_manager = managers
    user = user_manager.create_user("Alice", "alice@example.com")
    session = session_manager.create_session(user.id, title="历史会话")

    loaded = session_manager.load_session(session.id, user.id)

    assert loaded.id == session.id
    assert session_manager.get_active_session() == session


def test_cross_user_session_access_is_rejected(managers: tuple[UserManager, SessionManager]) -> None:
    user_manager, session_manager = managers
    owner = user_manager.create_user("Alice", "alice@example.com")
    other = user_manager.create_user("Bob", "bob@example.com")
    session = session_manager.create_session(owner.id, title="私有会话")

    with pytest.raises(SessionManagerError, match="无权访问其他用户的会话"):
        session_manager.load_session(session.id, other.id)


def test_switch_to_missing_session_raises(managers: tuple[UserManager, SessionManager]) -> None:
    _, session_manager = managers

    with pytest.raises(SessionManagerError, match="未找到会话"):
        session_manager.load_session(UUID(int=999))
