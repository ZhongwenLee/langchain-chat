from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from src import ChatChunk, ChatEngine, SessionManager, TUIApp, UserManager
from src.chat_engine import ChatResponse, TokenUsage
from src.config_manager import AppConfig, ConfigBundle, EnvironmentConfig, LoggingConfig, SecretConfig
from src.storage import SQLiteStorageBackend
from src.ui_protocol import UIEvent


@dataclass
class FakeClaude:
    model_name: str = "claude-3-haiku"
    reply: str = ""

    async def astream(self, messages, **kwargs):
        yield ChatChunk(content=self.reply[:1])
        yield ChatChunk(content=self.reply[1:], usage=TokenUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5), is_final=True)

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
def app(chat_engine: ChatEngine, session_manager: SessionManager, user_manager: UserManager) -> TUIApp:
    return TUIApp(chat_engine=chat_engine, session_manager=session_manager, user_manager=user_manager)


@pytest.fixture()
def chat_engine(session_manager: SessionManager, user_manager: UserManager) -> ChatEngine:
    user = user_manager.get_current_user()
    assert user is not None
    session_manager.create_session(user.id)
    return ChatEngine(session_manager=session_manager, user_manager=user_manager, model=FakeClaude(reply="已收到"))


def test_tui_state_and_actions(app: TUIApp) -> None:
    state = app.build_state()
    assert state.current_user is not None
    assert state.active_session is not None
    assert "model_name" in state.metadata
    assert app.get_status_summary().startswith("模型 claude-3-haiku")
    assert any(action.key == "presets" for action in app.get_menu_actions())
    assert any(action.key == "user-config" for action in app.get_menu_actions())

    pause_result = app.handle_event(UIEvent(name="pause"))
    resume_result = app.handle_event(UIEvent(name="resume"))
    assert pause_result.ok is True
    assert resume_result.ok is True


def test_tui_preset_and_user_config_flow(app: TUIApp) -> None:
    created = app.create_user_preset("个人助手", "请用简洁中文回答", "claude-3-haiku")
    listed = app.list_user_presets()
    config = app.update_user_config({"theme": "dark", "language": "zh-CN", "default_model": "claude-3-haiku", "active_preset_id": str(created.id), "preferences": {"font_size": "16"}})

    assert created in listed
    assert config.theme == "dark"
    assert config.active_preset_id == created.id
    fetched = app.get_user_config()
    assert fetched.preferences["font_size"] == "16"
    assert app.delete_user_preset(created.id) is True


@pytest.mark.asyncio
async def test_tui_send_and_stream_message(app: TUIApp) -> None:
    send_result = await app.send_message("你好")
    assert send_result.ok is True
    assert send_result.message == "已收到"
    assert send_result.data["tokens"] == 5
    assert send_result.data["model_name"] == "claude-3-haiku"

    stream_result = await app.stream_message("再来一次")
    assert stream_result.ok is True
    assert stream_result.message == "已收到"
    assert stream_result.data["tokens"] == 5


@pytest.mark.asyncio
async def test_tui_session_management_and_preview(app: TUIApp, user_manager: UserManager, session_manager: SessionManager) -> None:
    user = user_manager.get_current_user()
    assert user is not None

    create_result = app.create_session("项目讨论")
    assert create_result.ok is True

    sessions = session_manager.list_sessions(user.id)
    target = sessions[-1]

    select_result = app.select_session(target.id)
    rename_result = app.rename_session(target.id, "新标题")
    switch_result = app.switch_user(next(u.id for u in user_manager.list_users() if u.username == "bob"))

    assert select_result.ok is True
    assert rename_result.ok is True
    assert switch_result.ok is True

    preview = app.get_preview()
    assert preview is not None


@pytest.mark.asyncio
async def test_tui_search_export_and_stats(app: TUIApp, session_manager: SessionManager, user_manager: UserManager, tmp_path: Path) -> None:
    user = user_manager.get_current_user()
    assert user is not None
    session = session_manager.get_active_session()
    assert session is not None

    session_manager.add_message(session.id, "user", "搜索关键词在这里", user_id=user.id)
    session_manager.add_message(session.id, "assistant", "好的，我记住了", user_id=user.id, auto_title=False)

    hits = app.search_messages("关键词")
    assert hits and hits[0].matched_keyword == "关键词"

    export_info = app.export_session_markdown(session.id, export_dir=tmp_path)
    assert Path(export_info.path).exists()
    assert export_info.message_count >= 2

    stats = app.get_session_stats(session.id)
    assert stats.total_tokens >= stats.prompt_tokens
    assert stats.message_count >= 2

    event_result = app.handle_event(UIEvent(name="search_messages", payload={"keyword": "关键词"}))
    assert event_result.ok is True


@pytest.mark.asyncio
async def test_chat_engine_stream_keeps_token_usage(session_manager: SessionManager, user_manager: UserManager) -> None:
    user = user_manager.get_current_user()
    assert user is not None
    engine = ChatEngine(session_manager=session_manager, user_manager=user_manager, model=FakeClaude(reply="流式回复"))
    chunks = []
    async for chunk in engine.astream("测试", user_id=user.id):
        chunks.append(chunk)

    assert chunks[-1].is_final is True
    assert chunks[-1].usage.total_tokens == 5
