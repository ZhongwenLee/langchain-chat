from __future__ import annotations

from dataclasses import dataclass

import pytest

from src import ChatChunk, ChatEngine, ChatTurn, MessageRole, SessionManager, UserManager
from src.chat_engine import TokenUsage
from src.models import Session, User
from src.storage import SQLiteStorageBackend
from src.config_manager import AppConfig, ConfigBundle, EnvironmentConfig, LoggingConfig, SecretConfig


@dataclass
class FakeClaude:
    model_name: str = "claude-3-haiku"
    reply: str = ""  # 允许测试里直接覆盖返回内容。

    async def astream(self, messages, **kwargs):
        # 这里模拟真实模型的流式行为：先输出两段增量，再输出结束统计。
        yield ChatChunk(content=self.reply[: len(self.reply) // 2])
        yield ChatChunk(content=self.reply[len(self.reply) // 2 :], usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15))

    async def ainvoke(self, messages, **kwargs):
        return ChatChunk(content=self.reply, is_final=True, usage=TokenUsage(prompt_tokens=8, completion_tokens=4, total_tokens=12))


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
    return manager


@pytest.fixture()
def session_manager(backend: SQLiteStorageBackend, user_manager: UserManager) -> SessionManager:
    return SessionManager(storage=backend, user_manager=user_manager)


@pytest.mark.asyncio
async def test_chat_engine_stream_and_memory(session_manager: SessionManager, user_manager: UserManager) -> None:
    user = user_manager.get_current_user()
    assert user is not None
    session = session_manager.create_session(user.id)
    engine = ChatEngine(session_manager=session_manager, user_manager=user_manager, model=FakeClaude(reply="你好，世界"))

    response = await engine.ask("第一轮问题", session_id=session.id, user_id=user.id)
    assert response.content == "你好，世界"
    assert response.usage.total_tokens == 12

    chunks = []
    async for chunk in engine.astream("第二轮问题", session_id=session.id, user_id=user.id):
        chunks.append(chunk)

    assert chunks[-1].is_final is True
    assert chunks[-1].content == "你好，世界"

    messages = session_manager.list_messages(session.id, user.id)
    assert [m.role for m in messages] == [MessageRole.USER, MessageRole.ASSISTANT, MessageRole.USER, MessageRole.ASSISTANT]

    built = engine.build_messages(session.id, user_id=user.id, extra_turns=[ChatTurn(role=MessageRole.USER, content="补充问题")])
    assert built[0]["role"] == "user"
    assert built[-1]["content"] == "补充问题"


@pytest.mark.asyncio
async def test_chat_engine_switch_model(session_manager: SessionManager, user_manager: UserManager) -> None:
    user = user_manager.get_current_user()
    assert user is not None
    session = session_manager.create_session(user.id)
    engine = ChatEngine(session_manager=session_manager, user_manager=user_manager, model=FakeClaude(reply="A"))
    engine.switch_model(FakeClaude(model_name="claude-3-sonnet", reply="B"))

    response = await engine.ask("切换模型测试", session_id=session.id, user_id=user.id)
    assert response.model_name == "claude-3-sonnet"
    assert response.content == "B"
