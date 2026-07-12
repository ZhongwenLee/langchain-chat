from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Protocol
from uuid import UUID

from pydantic import BaseModel

from .models import Message, MessageRole, Session
from .session_manager import SessionManager, SessionManagerError
from .user_manager import UserManager


@dataclass(frozen=True)
class TokenUsage:
    """一次模型调用的 token 统计结果。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class ChatChunk:
    """流式输出中的一个增量片段。"""

    content: str
    is_final: bool = False
    usage: TokenUsage | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatResponse:
    """非流式调用的统一返回结构。"""

    content: str
    usage: TokenUsage
    assistant_message: Message
    model_name: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatTurn:
    """单轮对话输入。"""

    role: MessageRole
    content: str


class ChatModel(Protocol):
    """对底层大模型的最小能力抽象。"""

    model_name: str

    async def astream(self, messages: list[dict[str, str]], **kwargs: Any) -> AsyncIterator[ChatChunk]: ...

    async def ainvoke(self, messages: list[dict[str, str]], **kwargs: Any) -> ChatChunk: ...


class ChatClaude:
    """Claude 模型适配器。

    这里刻意只定义“聊天引擎所需要的最小能力”，而不是直接依赖某个具体 LangChain
    版本的复杂对象。这样做的好处是：
    1. 业务层可以稳定依赖本模块；
    2. 未来无论是 LangChain Anthropic、直接 Anthropic SDK，还是本地 mock 实现，
       都可以通过同一协议接入；
    3. 测试时可以轻松注入假实现，避免依赖外部网络。
    """

    def __init__(
        self,
        *,
        model_name: str,
        api_key: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        client: Any | None = None,
    ) -> None:
        self.model_name = model_name
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = client or self._build_client()

    def _build_client(self) -> Any:
        # 这里优先尝试对接 langchain_anthropic；如果运行环境没有安装相关依赖，
        # 则保留一个明确的占位错误，避免在真正发起请求时才出现更难定位的问题。
        try:
            from langchain_anthropic import ChatAnthropic
        except Exception as exc:  # pragma: no cover - 依赖缺失时的兜底路径
            raise RuntimeError(
                "缺少 langchain_anthropic 依赖，无法创建 ChatClaude 客户端"
            ) from exc

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "temperature": self.temperature,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        return ChatAnthropic(**kwargs)

    async def astream(self, messages: list[dict[str, str]], **kwargs: Any) -> AsyncIterator[ChatChunk]:
        # LangChain 标准流式接口通常会返回 AIMessageChunk；这里把它统一收敛成 ChatChunk，
        # 这样上层就不必关心不同 SDK 的实现细节。
        async for chunk in self._client.astream(messages, **kwargs):
            content = getattr(chunk, "content", "") or ""
            usage = _extract_usage(chunk)
            yield ChatChunk(content=content, is_final=False, usage=usage, metadata=_extract_metadata(chunk))

    async def ainvoke(self, messages: list[dict[str, str]], **kwargs: Any) -> ChatChunk:
        result = await self._client.ainvoke(messages, **kwargs)
        content = getattr(result, "content", "") or ""
        return ChatChunk(content=content, is_final=True, usage=_extract_usage(result), metadata=_extract_metadata(result))


@dataclass
class ChatEngine:
    """对话引擎核心。

    这个类负责把“会话管理 + 历史上下文 + 模型调用”串成一条完整链路，
    让上层只需要传入用户消息，就能拿到可流式展示的回复，并且把消息自动保存到会话里。
    """

    session_manager: SessionManager
    user_manager: UserManager
    model: ChatModel
    memory_window: int = 20
    system_prompt: str | None = None

    def get_active_session(self) -> Session | None:
        return self.session_manager.get_active_session()

    def switch_model(self, model: ChatModel) -> None:
        # 会话内切换模型时，不改变当前会话，只替换后续调用所使用的模型。
        # 这样用户在同一对话线程中也能根据任务需要切换不同能力的模型。
        self.model = model

    def build_messages(
        self,
        session_id: str | UUID | None = None,
        *,
        user_id: str | UUID | None = None,
        extra_turns: list[ChatTurn] | None = None,
        prompt_override: str | None = None,
    ) -> list[dict[str, str]]:
        session = self._resolve_session(session_id, user_id=user_id)
        history = self.session_manager.list_messages(session.id, user_id)
        turns: list[dict[str, str]] = []

        # 系统提示词应始终位于消息首位，因为它定义了整轮对话的行为边界。
        system_prompt = prompt_override if prompt_override is not None else self.system_prompt
        if system_prompt:
            turns.append({"role": "system", "content": system_prompt})

        # 为了控制上下文长度，这里只保留最近 N 条消息；这样可以避免 prompt 无限膨胀。
        # 这种做法是最实用的“滑动窗口记忆”，后续如需更复杂的摘要记忆，可以在这里替换。
        for message in history[-self.memory_window :]:
            turns.append({"role": message.role.value, "content": message.content})

        if extra_turns:
            for turn in extra_turns:
                turns.append({"role": turn.role.value, "content": turn.content})

        return turns

    def build_prompt_for_session(
        self,
        session_id: str | UUID | None = None,
        *,
        user_id: str | UUID | None = None,
        prompt_override: str | None = None,
    ) -> str | None:
        """生成当前会话对应的系统提示词。"""

        session = self._resolve_session(session_id, user_id=user_id)
        messages = self.session_manager.list_messages(session.id, user_id)
        active_prompt = prompt_override if prompt_override is not None else self.system_prompt
        if active_prompt:
            return active_prompt

        # 如果当前没有显式系统提示词，则尝试从历史会话里提取首个 system 消息。
        # 这样即使切换模型或恢复会话，历史上下文也不会丢失预设角色信息。
        for message in messages:
            if message.role == MessageRole.SYSTEM:
                return message.content
        return None

    async def ask(
        self,
        content: str,
        *,
        session_id: str | UUID | None = None,
        user_id: str | UUID | None = None,
        model_kwargs: dict[str, Any] | None = None,
        save_user_message: bool = True,
        save_assistant_message: bool = True,
        auto_create_session: bool = True,
    ) -> ChatResponse:
        """执行一次完整的非流式对话。"""

        session = self._ensure_session(session_id, user_id=user_id, auto_create_session=auto_create_session)
        if save_user_message:
            self.session_manager.add_message(session.id, MessageRole.USER, content, user_id=user_id)

        messages = self.build_messages(session.id, user_id=user_id)
        result = await self.model.ainvoke(messages, **(model_kwargs or {}))
        assistant_message = self.session_manager.add_message(
            session.id,
            MessageRole.ASSISTANT,
            result.content,
            user_id=user_id,
            auto_title=False,
        ) if save_assistant_message else Message.model_validate(
            {
                "session_id": session.id,
                "role": MessageRole.ASSISTANT,
                "content": result.content,
                "sequence": -1,
                "metadata": {},
            }
        )
        return ChatResponse(content=result.content, usage=result.usage or TokenUsage(), assistant_message=assistant_message, model_name=self.model.model_name, metadata=result.metadata)

    async def astream(
        self,
        content: str,
        *,
        session_id: str | UUID | None = None,
        user_id: str | UUID | None = None,
        model_kwargs: dict[str, Any] | None = None,
        auto_create_session: bool = True,
    ) -> AsyncIterator[ChatChunk]:
        """执行一次流式对话，边生成边向调用方输出。"""

        session = self._ensure_session(session_id, user_id=user_id, auto_create_session=auto_create_session)
        self.session_manager.add_message(session.id, MessageRole.USER, content, user_id=user_id)
        messages = self.build_messages(session.id, user_id=user_id)
        buffer: list[str] = []
        usage: TokenUsage | None = None

        async for chunk in self.model.astream(messages, **(model_kwargs or {})):
            if chunk.content:
                buffer.append(chunk.content)
            if chunk.usage is not None:
                usage = chunk.usage
            yield chunk

        final_content = "".join(buffer)
        assistant_message = self.session_manager.add_message(session.id, MessageRole.ASSISTANT, final_content, user_id=user_id, auto_title=False)
        # 最后一个 chunk 统一携带汇总后的 token 信息，方便前端在流结束时一次性展示统计。
        yield ChatChunk(content=final_content, is_final=True, usage=usage or TokenUsage(), metadata={"message_id": str(assistant_message.id), "model_name": self.model.model_name})

    def _resolve_session(self, session_id: str | UUID | None, *, user_id: str | UUID | None = None) -> Session:
        session = self.session_manager.get_active_session() if session_id is None else self.session_manager.get_session(session_id)
        if session is None:
            raise SessionManagerError("没有可用的会话，请先创建或选择一个会话")
        if user_id is not None:
            owner = self.user_manager.get_user(user_id)
            if owner is None:
                raise SessionManagerError(f"未找到用户: {user_id}")
            if session.user_id != owner.id:
                raise SessionManagerError("无权访问其他用户的会话")
        return session

    def _ensure_session(self, session_id: str | UUID | None, *, user_id: str | UUID | None, auto_create_session: bool) -> Session:
        if session_id is not None:
            return self._resolve_session(session_id, user_id=user_id)
        session = self.session_manager.get_active_session()
        if session is not None:
            return session
        if not auto_create_session:
            raise SessionManagerError("当前没有活跃会话")
        return self.session_manager.create_session(user_id=user_id, auto_select=True)


@dataclass(frozen=True)
class EngineStats:
    """用于对外展示的引擎统计信息。"""

    session_id: UUID
    model_name: str
    total_messages: int
    total_tokens: int
    last_updated_at: datetime


class ChatEngineInspector:
    """轻量统计器。

    这个辅助类不参与生成逻辑，只负责从会话和消息中提取摘要信息，
    方便 UI、日志系统或调试面板读取当前对话状态。
    """

    def __init__(self, session_manager: SessionManager, engine: ChatEngine) -> None:
        self.session_manager = session_manager
        self.engine = engine

    def collect(self, session_id: str | UUID | None = None, *, user_id: str | UUID | None = None) -> EngineStats:
        session = self.session_manager.get_active_session() if session_id is None else self.session_manager.get_session(session_id)
        if session is None:
            raise SessionManagerError("没有可统计的会话")
        messages = self.session_manager.list_messages(session.id, user_id)
        total_tokens = sum(_estimate_tokens(message.content) for message in messages)
        return EngineStats(
            session_id=session.id,
            model_name=self.engine.model.model_name,
            total_messages=len(messages),
            total_tokens=total_tokens,
            last_updated_at=session.updated_at,
        )


def _extract_metadata(item: Any) -> dict[str, Any]:
    usage_metadata = getattr(item, "response_metadata", None) or getattr(item, "metadata", None) or {}
    return dict(usage_metadata) if isinstance(usage_metadata, dict) else {}


def _extract_usage(item: Any) -> TokenUsage | None:
    # 不同 SDK 的 usage 字段命名并不完全一致，因此这里做“尽量识别”的兼容提取。
    metadata = getattr(item, "usage_metadata", None) or getattr(item, "response_metadata", None) or {}
    if not isinstance(metadata, dict):
        return None
    input_tokens = metadata.get("input_tokens") or metadata.get("prompt_tokens") or 0
    output_tokens = metadata.get("output_tokens") or metadata.get("completion_tokens") or 0
    if not input_tokens and not output_tokens:
        return None
    return TokenUsage(prompt_tokens=int(input_tokens), completion_tokens=int(output_tokens), total_tokens=int(input_tokens) + int(output_tokens))


def _estimate_tokens(text: str) -> int:
    # 在拿不到模型原生 usage 的情况下，使用非常保守的近似统计；
    # 这不是账单级别的精确计数，但足以支持会话侧的可观测性和 UI 展示。
    return max(1, len(text) // 4)
