from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from src import ChatChunk, ChatEngine, ChatTurn, MessageRole, PresetManager, SessionManager, UserManager
from src.chat_engine import ChatResponse, TokenUsage
from src.config_manager import AppConfig, ConfigBundle, EnvironmentConfig, LoggingConfig, SecretConfig
from src.models import Message, Preset, PresetScope, Session, User, UserConfig
from src.storage import SQLiteStorageBackend, StoragePagination, StorageSearchQuery


@dataclass
class FakeModel:
    model_name: str = "claude-3-haiku"
    reply: str = ""

    async def astream(self, messages, **kwargs):
        midpoint = max(1, len(self.reply) // 2)
        yield ChatChunk(content=self.reply[:midpoint])
        yield ChatChunk(
            content=self.reply[midpoint:],
            is_final=False,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    async def ainvoke(self, messages, **kwargs):
        return ChatChunk(
            content=self.reply,
            is_final=True,
            usage=TokenUsage(prompt_tokens=8, completion_tokens=4, total_tokens=12),
            metadata={"source": "fake-model"},
        )


class TestFailure(RuntimeError):
    pass


async def run_all() -> None:
    config = ConfigBundle(
        app=AppConfig(app_name="self-test", debug=True, environment=EnvironmentConfig()),
        logging=LoggingConfig(version=1),
        secrets=SecretConfig(api_key="test-key", database_url="sqlite:///:memory:"),
        presets={
            "system_presets": [
                {
                    "id": "default-assistant",
                    "name": "默认助手",
                    "description": "通用对话场景，适合日常问答、总结和轻量分析。",
                    "scope": "global",
                    "model_name": "gpt-4.1",
                    "temperature": 0.7,
                    "prompt_template": "你是一个专业、可靠、表达清晰的中文 AI 助手。\n你需要优先给出直接、准确、可执行的回答。\n如果信息不足，请明确指出缺失内容，并给出下一步建议。",
                }
            ],
            "user_preset_schema": {
                "enabled": True,
                "storage_hint": "预留用户自定义预设入口，后续可接入数据库或文件系统。",
            },
        },
    )
    backend = SQLiteStorageBackend(database_url="sqlite:///:memory:", config=config)
    await backend.aconnect()

    user_manager = UserManager(storage=backend)
    session_manager = SessionManager(storage=backend, user_manager=user_manager)
    preset_manager = PresetManager(config=config)

    print("[1/10] 用户与配置")
    alice = user_manager.create_user(username="alice", email="alice@example.com")
    bob = user_manager.create_user(username="bob", email="bob@example.com")
    assert user_manager.get_current_user() is not None
    assert user_manager.get_user_config(alice.id).user_id == alice.id
    user_manager.set_user_preference("font_size", "16", user_id=alice.id)
    assert user_manager.get_user_config(alice.id).preferences["font_size"] == "16"
    assert len(user_manager.list_users()) == 2

    print("[2/10] 会话创建、重命名、归档")
    session = session_manager.create_session(alice.id, preset_hint="聊天测试")
    assert session.user_id == alice.id
    renamed = session_manager.rename_session(session.id, "正式标题", user_id=alice.id)
    assert renamed.title == "正式标题"
    archived = session_manager.archive_session(session.id, user_id=alice.id)
    assert archived.is_archived is True

    print("[3/10] 消息写入与顺序")
    session_manager.add_message(session.id, MessageRole.USER, "你好，系统", user_id=alice.id)
    session_manager.add_message(session.id, MessageRole.ASSISTANT, "你好！", user_id=alice.id, auto_title=False)
    messages = session_manager.list_messages(session.id, alice.id)
    assert [m.sequence for m in messages] == [0, 1]
    assert [m.role for m in messages] == [MessageRole.USER, MessageRole.ASSISTANT]

    print("[4/10] 搜索、分页与导出")
    page = backend.list("users", StoragePagination(page=1, page_size=1))
    assert page.total >= 2 and len(page.items) == 1
    search = backend.search("users", StorageSearchQuery(keyword="alice", fields=("username",)))
    assert search.total == 1
    exported = backend.export("users")
    assert exported.format == "json"
    assert "alice@example.com" in str(exported.payload)

    print("[5/10] 模型层校验")
    preset = Preset(
        owner_id=alice.id,
        name="默认预设",
        scope=PresetScope.PRIVATE,
        prompt_template="你是一个有帮助的助手",
        model_name="claude-3-haiku",
    )
    assert preset.scope == PresetScope.PRIVATE
    assert isinstance(User.model_validate(alice.model_dump()), User)
    assert isinstance(Session.model_validate(session.model_dump()), Session)
    assert isinstance(Message.model_validate(messages[0].model_dump()), Message)
    assert isinstance(UserConfig.model_validate(user_manager.get_user_config(alice.id).model_dump()), UserConfig)

    print("[6/10] 聊天引擎非流式")
    engine = ChatEngine(session_manager=session_manager, user_manager=user_manager, model=FakeModel(reply="模型回复内容"), system_prompt="你是测试助手")
    response: ChatResponse = await engine.ask("第一轮问题", session_id=session.id, user_id=alice.id)
    assert response.content == "模型回复内容"
    assert response.usage.total_tokens == 12
    assert response.assistant_message.content == "模型回复内容"
    assert response.metadata["source"] == "fake-model"

    print("[7/10] 聊天引擎流式")
    stream_chunks: list[ChatChunk] = []
    async for chunk in engine.astream("第二轮问题", session_id=session.id, user_id=alice.id):
        stream_chunks.append(chunk)
    assert stream_chunks[-1].is_final is True
    assert stream_chunks[-1].content == "模型回复内容"
    assert stream_chunks[-1].metadata["model_name"] == "claude-3-haiku"

    print("[8/10] 构建上下文、切换模型、越权校验")
    built = engine.build_messages(session.id, user_id=alice.id, extra_turns=[ChatTurn(role=MessageRole.USER, content="补充问题")])
    assert built[0]["role"] == "system"
    assert built[-1]["content"] == "补充问题"
    engine.switch_model(FakeModel(model_name="claude-3-sonnet", reply="切换后回复"))
    response2 = await engine.ask("切换模型测试", session_id=session.id, user_id=alice.id)
    assert response2.model_name == "claude-3-sonnet"
    assert response2.content == "切换后回复"

    try:
        session_manager.list_messages(session.id, bob.id)
    except Exception:
        pass
    else:
        raise TestFailure("越权读取不应通过")

    print("[9/10] 预设管理器与 system prompt")
    builtin_presets = preset_manager.list_builtin_presets()
    assert len(builtin_presets) >= 1
    default_preset = preset_manager.select_preset()
    assert default_preset.model_name
    system_prompt = preset_manager.build_system_prompt(default_preset.id)
    assert default_preset.name in system_prompt
    prompt_context = preset_manager.create_session_prompt_context(default_preset.id)
    assert prompt_context["preset_id"] == str(default_preset.id)
    assert prompt_context["system_prompt"] == system_prompt
    assert preset_manager.get_preset(default_preset.name).id == default_preset.id

    print("[10/10] 配置加载与会话生命周期补充")
    assert config.app.app_name == "self-test"
    assert config.secrets.api_key == "test-key"
    assert session_manager.set_active_session(session.id, user_id=alice.id).id == session.id
    deleted = session_manager.delete_session(session.id, user_id=alice.id)
    assert deleted is True
    assert session_manager.get_session(session.id) is None
    assert session_manager.get_active_session() is None

    deleted_user = user_manager.delete_user(bob.id)
    assert deleted_user is True
    assert user_manager.get_user(bob.id) is None

    await backend.aclose()
    print("\n全部自检通过。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an end-to-end self test for the chat app.")
    return parser


def main() -> None:
    build_parser().parse_args()
    asyncio.run(run_all())


if __name__ == "__main__":
    main()
