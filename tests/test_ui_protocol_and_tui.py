from __future__ import annotations

from dataclasses import dataclass

import pytest

from src import ChatChunk, ChatEngine, MessageRole, SessionManager, TUIApp, UserManager
from src.chat_engine import TokenUsage
from src.config_manager import AppConfig, ConfigBundle, EnvironmentConfig, LoggingConfig, SecretConfig
from src.storage import SQLiteStorageBackend
from src.ui_protocol import ConversationPreview, MenuAction, UIEvent, UIKind, UIResult, UIState


@dataclass
class FakeClaude:
    model_name: str = "claude-3-haiku"
    reply: str = ""

    async def astream(self, messages, **kwargs):
        yield ChatChunk(content=self.reply, is_final=True, usage=TokenUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5))

    async def ainvoke(self, messages, **kwargs):
        return ChatChunk(content=self.reply, is_final=True, usage=TokenUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5))


@pytest.fixture()
def backend() -> SQLiteStorageBackend:
    config = ConfigBundle(
        app=AppConfig(app_name="test", debug=True, environment=EnvironmentConfig()),
        logging=LoggingConfig(version=1),
        secrets=SecretConfig(api_key="test-key", database_url="sqlite:///:memory:"),
    )
    db = SQLiteStorageBackend(database_url="sqlite:///:memory:", config=config)
    db.connect()
    return db


@pytest.fixture()
def user_manager(backend: SQLiteStorageBackend) -> UserManager:
    manager = UserManager(storage=backend)
    manager.create_user(username="alice", email="alice@example.com")
    manager.create_user(username="bob", email="bob@example.com")
    return manager


@pytest.fixture()
def session_manager(backend: SQLiteStorageBackend, user_manager: UserManager) -> SessionManager:
    return SessionManager(storage=backend, user_manager=user_manager)


@pytest.fixture()
def chat_engine(session_manager: SessionManager, user_manager: UserManager) -> ChatEngine:
    user = user_manager.get_current_user()
    assert user is not None
    session_manager.create_session(user.id)
    return ChatEngine(session_manager=session_manager, user_manager=user_manager, model=FakeClaude(reply="已收到"))


@pytest.fixture()
def app(chat_engine: ChatEngine, session_manager: SessionManager, user_manager: UserManager) -> TUIApp:
    return TUIApp(chat_engine=chat_engine, session_manager=session_manager, user_manager=user_manager)


def test_ui_protocol_basic_types() -> None:
    state = UIState(kind=UIKind.TUI, status_message="ready")
    action = MenuAction(key="refresh", title="刷新状态")
    result = UIResult(ok=True, message="done")
    preview = ConversationPreview(user_text="你好")
    event = UIEvent(name="refresh")

    assert state.kind is UIKind.TUI
    assert action.title == "刷新状态"
    assert result.ok is True
    assert preview.user_text == "你好"
    assert event.name == "refresh"


def test_tui_app_build_state_and_events(app: TUIApp) -> None:
    state = app.build_state()

    assert state.kind is UIKind.TUI
    assert state.current_user is not None
    assert state.active_session is not None
    assert state.metadata["user_count"] == 2
    assert state.metadata["session_count"] >= 1

    refresh_result = app.handle_event(UIEvent(name="refresh"))
    assert refresh_result.ok is True


def test_tui_app_session_and_user_actions(app: TUIApp, user_manager: UserManager, session_manager: SessionManager) -> None:
    current_user = user_manager.get_current_user()
    assert current_user is not None

    create_result = app.create_session("项目讨论")
    assert create_result.ok is True
    assert create_result.message == "已创建会话: 项目讨论"

    sessions = session_manager.list_sessions(current_user.id)
    target_session = sessions[-1]

    select_result = app.select_session(target_session.id)
    assert select_result.ok is True

    rename_result = app.rename_session(target_session.id, "新标题")
    assert rename_result.ok is True
    assert rename_result.message == "已重命名为: 新标题"

    switch_result = app.switch_user(next(user.id for user in user_manager.list_users() if user.username == "bob"))
    assert switch_result.ok is True
    assert "bob" in switch_result.message


@pytest.mark.asyncio
async def test_tui_app_send_message_and_preview(app: TUIApp) -> None:
    result = await app.send_message("你好，TUI")

    assert result.ok is True
    assert result.message == "已收到"
    assert result.data["model_name"] == "claude-3-haiku"

    preview = app.get_preview()
    assert preview is not None
    assert preview.user_text == "你好，TUI"
    assert preview.assistant_text == "已收到"
