from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from src.config_manager import ConfigBundle, AppConfig, EnvironmentConfig, LoggingConfig, SecretConfig
from src.models import PresetScope, UserConfig
from src.storage import SQLiteStorageBackend
from src.user_manager import UserManager, UserManagerError


@pytest.fixture()
def sqlite_backend(tmp_path: Path) -> SQLiteStorageBackend:
    config = ConfigBundle(
        app=AppConfig(app_name="test-app", environment=EnvironmentConfig()),
        logging=LoggingConfig(version=1),
        secrets=SecretConfig(api_key="test-key", database_url=f"sqlite:///{tmp_path / 'users.db'}"),
    )
    backend = SQLiteStorageBackend(database_url=config.secrets.database_url, config=config)
    backend.connect()
    yield backend
    backend.close()


@pytest.fixture()
def user_manager(sqlite_backend: SQLiteStorageBackend) -> UserManager:
    return UserManager(sqlite_backend)


def test_create_user_and_default_current_user(user_manager: UserManager) -> None:
    user = user_manager.create_user("Alice", "alice@example.com")

    assert user.username == "Alice"
    assert user_manager.get_current_user() == user
    config = user_manager.get_user_config(user.id)
    assert config.user_id == user.id
    assert config.preferences == {}


def test_username_must_be_unique(user_manager: UserManager) -> None:
    user_manager.create_user("Alice", "alice@example.com")

    with pytest.raises(UserManagerError, match="用户名已存在"):
        user_manager.create_user("Alice", "alice2@example.com")


def test_switch_current_user_changes_active_context(user_manager: UserManager) -> None:
    first = user_manager.create_user("Alice", "alice@example.com", preferences={"theme": "dark"})
    second = user_manager.create_user("Bob", "bob@example.com")

    switched = user_manager.switch_current_user(second.id)

    assert switched.id == second.id
    assert user_manager.get_current_user().id == second.id
    assert user_manager.get_user_config(first.id).preferences == {"theme": "dark"}


def test_update_preferences_merges_existing_values(user_manager: UserManager) -> None:
    user = user_manager.create_user("Alice", "alice@example.com", preferences={"theme": "dark"})

    result = user_manager.set_user_preference("language", "en-US", user.id)
    config = user_manager.get_user_config(user.id)

    assert result.user_id == user.id
    assert result.preferences == {"theme": "dark", "language": "en-US"}
    assert config.preferences == {"theme": "dark", "language": "en-US"}


def test_delete_user_cascades_related_data(user_manager: UserManager, sqlite_backend: SQLiteStorageBackend) -> None:
    user = user_manager.create_user("Alice", "alice@example.com")
    user_manager.get_user_config(user.id)

    assert user_manager.delete_user(user.id) is True
    assert user_manager.get_user(user.id) is None
    assert user_manager._get_user_config(user.id) is None


def test_delete_user_removes_current_user(user_manager: UserManager) -> None:
    user = user_manager.create_user("Alice", "alice@example.com")
    assert user_manager.current_user_id == user.id

    assert user_manager.delete_user(user.id) is True
    assert user_manager.get_current_user() is None


def test_switch_to_missing_user_raises(user_manager: UserManager) -> None:
    with pytest.raises(UserManagerError, match="未找到用户"):
        user_manager.switch_current_user(UUID(int=999))


def test_user_preset_crud_and_active_preset(user_manager: UserManager) -> None:
    user = user_manager.create_user("Alice", "alice@example.com")

    created = user_manager.create_user_preset("Coding", "You are helpful", "gpt-4.1", scope=PresetScope.PRIVATE, user_id=user.id)
    listed = user_manager.list_user_presets(user.id)
    fetched = user_manager.get_user_preset(created.id, user.id)
    updated = user_manager.update_user_preset(created.id, name="Coding Pro", temperature=0.2, user_id=user.id)
    config = user_manager.set_active_preset(created.id, user.id)

    assert listed and listed[0].id == created.id
    assert fetched is not None and fetched.owner_id == user.id
    assert updated.name == "Coding Pro"
    assert config.active_preset_id == created.id
    assert user_manager.delete_user_preset(created.id, user.id) is True
    assert user_manager.get_user_preset(created.id, user.id) is None
