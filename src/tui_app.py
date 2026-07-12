from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from .chat_engine import ChatEngine, ChatEngineInspector
from .models import MessageRole, Session, User
from .session_manager import SessionManager, SessionManagerError, SessionSummary
from .ui_protocol import ConversationPreview, MenuAction, UIEvent, UIKind, UIResult, UIState
from .user_manager import UserManager, UserManagerError


@dataclass(frozen=True)
class SessionSearchHit:
    session_id: UUID
    session_title: str
    message_id: UUID
    role: MessageRole
    sequence: int
    snippet: str
    matched_keyword: str


@dataclass(frozen=True)
class SessionExportInfo:
    session_id: UUID
    filename: str
    path: str
    message_count: int


@dataclass(frozen=True)
class SessionStats:
    session_id: UUID
    model_name: str
    message_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    last_updated_at: datetime


@dataclass
class TUIApp:
    chat_engine: ChatEngine
    session_manager: SessionManager
    user_manager: UserManager
    kind: UIKind = UIKind.TUI
    _state_cache: UIState = field(default_factory=lambda: UIState(kind=UIKind.TUI), init=False, repr=False)
    _paused: bool = field(default=False, init=False, repr=False)
    export_root: Path = field(default_factory=lambda: Path.home() / "langchain-chat-exports")

    def build_state(self) -> UIState:
        current_user = self.user_manager.get_current_user()
        active_session = self.session_manager.get_active_session()
        sessions = self.session_manager.list_sessions(current_user.id if current_user else None) if current_user else []
        metadata: dict[str, Any] = {
            "model_name": self.chat_engine.model.model_name,
            "paused": self._paused,
            "user_count": len(self.user_manager.list_users()),
            "session_count": len(sessions),
        }
        if active_session is not None:
            try:
                metadata["statistics"] = self.get_session_stats(active_session.id)
            except SessionManagerError:
                metadata["statistics"] = None
        state = UIState(kind=self.kind, current_user=current_user, active_session=active_session, sessions=sessions, status_message=self.get_status_summary(), metadata=metadata)
        self._state_cache = state
        return state

    def _format_message(self, text: str) -> str:
        normalized = text.replace("：", ":")
        return normalized.replace(":", ": ", 1) if ":" in normalized and ": " not in normalized else normalized

    def refresh_state(self) -> UIState:
        return self.build_state()

    def list_sessions(self) -> list[SessionSummary]:
        user = self.user_manager.get_current_user()
        return self.session_manager.list_sessions(user.id if user else None)

    def list_users(self) -> list[User]:
        return self.user_manager.list_users()

    def switch_user(self, user_id: str | UUID) -> UIResult:
        try:
            user = self.user_manager.switch_current_user(user_id)
            sessions = self.session_manager.list_sessions(user.id)
            self.session_manager.active_session_id = sessions[0].id if sessions else None
            self.refresh_state()
            return UIResult(ok=True, message=f"已切换到用户 {user.username}", data={"user_id": str(user.id)})
        except UserManagerError as exc:
            return UIResult(ok=False, message=str(exc))

    def create_session(self, title: str | None = None) -> UIResult:
        try:
            session = self.session_manager.create_session(title=title)
            self.refresh_state()
            return UIResult(ok=True, message=self._format_message(f"已创建会话：{session.title}"), data={"session_id": str(session.id)})
        except SessionManagerError as exc:
            return UIResult(ok=False, message=str(exc))

    def select_session(self, session_id: str | UUID) -> UIResult:
        try:
            session = self.session_manager.set_active_session(session_id)
            self.refresh_state()
            return UIResult(ok=True, message=self._format_message(f"已选中会话：{session.title}"), data={"session_id": str(session.id)})
        except SessionManagerError as exc:
            return UIResult(ok=False, message=str(exc))

    def rename_session(self, session_id: str | UUID, title: str) -> UIResult:
        try:
            session = self.session_manager.rename_session(session_id, title)
            self.refresh_state()
            return UIResult(ok=True, message=self._format_message(f"已重命名为：{session.title}"), data={"session_id": str(session.id), "title": session.title})
        except SessionManagerError as exc:
            return UIResult(ok=False, message=str(exc))

    async def send_message(self, content: str, *, session_id: str | UUID | None = None) -> UIResult:
        user = self.user_manager.get_current_user()
        response = await self.chat_engine.ask(content, session_id=session_id, user_id=user.id if user else None)
        self.refresh_state()
        return UIResult(ok=True, message=response.content, data={"tokens": response.usage.total_tokens, "model_name": response.model_name})

    async def stream_message(self, content: str, *, session_id: str | UUID | None = None) -> UIResult:
        user = self.user_manager.get_current_user()
        final_chunk = None
        async for chunk in self.chat_engine.astream(content, session_id=session_id, user_id=user.id if user else None):
            if chunk.is_final:
                final_chunk = chunk
        if final_chunk is None:
            return UIResult(ok=False, message="模型没有返回结果")
        self.refresh_state()
        return UIResult(ok=True, message=final_chunk.content, data={"tokens": final_chunk.usage.total_tokens if final_chunk.usage else 0, "model_name": self.chat_engine.model.model_name})

    def get_preview(self, session_id: str | UUID | None = None) -> ConversationPreview | None:
        user = self.user_manager.get_current_user()
        session = self.session_manager.get_active_session() if session_id is None else self.session_manager.get_session(session_id)
        if session is None and session_id is None:
            sessions = self.session_manager.list_sessions(user.id if user else None)
            session = sessions[0] if sessions else None
            if session is not None:
                session = self.session_manager.get_session(session.id)
        if session is None:
            return ConversationPreview(user_text="", assistant_text="")
        try:
            messages = self.session_manager.list_messages(session.id, user.id if user else None)
        except SessionManagerError:
            return ConversationPreview(user_text="", assistant_text="")
        if not messages:
            return ConversationPreview(user_text="", assistant_text="")
        user_text = next((m.content for m in reversed(messages) if m.role == MessageRole.USER), messages[-1].content)
        assistant_text = next((m.content for m in reversed(messages) if m.role == MessageRole.ASSISTANT), "")
        return ConversationPreview(user_text=user_text, assistant_text=assistant_text)

    def get_menu_actions(self) -> list[MenuAction]:
        return [
            MenuAction(key="search", title="搜索消息", description="按关键词检索历史消息"),
            MenuAction(key="export", title="导出会话 Markdown", description="导出完整会话到用户目录"),
            MenuAction(key="stats", title="查看 token 统计", description="查看当前会话 token 统计"),
        ]

    def get_status_summary(self) -> str:
        current_user = self._state_cache.current_user
        active_session = self._state_cache.active_session
        user_name = current_user.username if current_user else "未登录"
        session_title = active_session.title if active_session else "无活跃会话"
        return f"模型 {self.chat_engine.model.model_name} · 用户 {user_name} · 会话 {session_title}"

    def get_search_results(self, keyword: str, *, limit: int = 20) -> list[SessionSearchHit]:
        return self.search_messages(keyword, limit=limit)

    def get_session_stats(self, session_id: str | UUID | None = None) -> SessionStats:
        inspector = ChatEngineInspector(self.session_manager, self.chat_engine)
        stats = inspector.collect(session_id)
        messages = self.session_manager.list_messages(stats.session_id)
        prompt_tokens = sum(len(m.content) // 4 for m in messages if m.role in {MessageRole.USER, MessageRole.SYSTEM})
        completion_tokens = sum(len(m.content) // 4 for m in messages if m.role == MessageRole.ASSISTANT)
        return SessionStats(session_id=stats.session_id, model_name=stats.model_name, message_count=stats.total_messages, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=prompt_tokens + completion_tokens, last_updated_at=stats.last_updated_at)

    def export_session(self, session_id: str | UUID, *, export_dir: str | Path | None = None) -> SessionExportInfo:
        return self.export_session_markdown(session_id, export_dir=export_dir)

    def search_messages(self, keyword: str, *, limit: int = 20) -> list[SessionSearchHit]:
        user = self.user_manager.get_current_user()
        sessions = self.session_manager.list_sessions(user.id if user else None)
        hits: list[SessionSearchHit] = []
        for summary in sessions:
            messages = self.session_manager.list_messages(summary.id, user.id if user else None)
            for message in messages:
                if keyword.lower() in message.content.lower():
                    hits.append(SessionSearchHit(session_id=summary.id, session_title=summary.title, message_id=message.id, role=message.role, sequence=message.sequence, snippet=self._build_snippet(message.content, keyword), matched_keyword=keyword))
                    if len(hits) >= limit:
                        return hits
        return hits

    def export_session_markdown(self, session_id: str | UUID, *, export_dir: str | Path | None = None) -> SessionExportInfo:
        session = self.session_manager.get_session(session_id)
        if session is None:
            raise SessionManagerError(f"未找到会话: {session_id}")
        messages = self.session_manager.list_messages(session.id, session.user_id)
        root = Path(export_dir) if export_dir is not None else self.export_root / str(session.user_id)
        root.mkdir(parents=True, exist_ok=True)
        filename = f"session_{session.id}.md"
        path = root / filename
        path.write_text(self._render_session_markdown(session, messages), encoding="utf-8")
        return SessionExportInfo(session_id=session.id, filename=filename, path=str(path), message_count=len(messages))

    def handle_event(self, event: UIEvent | dict[str, Any]) -> UIResult:
        if isinstance(event, dict):
            event = UIEvent(name=str(event.get("name", "")), payload=dict(event.get("payload", {})))
        if event.name == "refresh":
            self.refresh_state()
            return UIResult(ok=True, message="已刷新")
        if event.name == "pause":
            self._paused = True
            return UIResult(ok=True, message="已暂停")
        if event.name == "resume":
            self._paused = False
            return UIResult(ok=True, message="已恢复")
        if event.name == "search_messages":
            hits = self.search_messages(str(event.payload.get("keyword", "")))
            return UIResult(ok=True, message=f"找到 {len(hits)} 条消息", data={"hits": [hit.__dict__ for hit in hits]})
        if event.name == "export_session":
            export_info = self.export_session_markdown(event.payload["session_id"], export_dir=event.payload.get("export_dir"))
            return UIResult(ok=True, message="会话已导出", data=export_info.__dict__)
        if event.name == "session_stats":
            stats = self.get_session_stats(event.payload.get("session_id"))
            return UIResult(ok=True, message="统计完成", data=stats.__dict__)
        return UIResult(ok=False, message=f"不支持的事件: {event.name}")

    def build_menu(self) -> list[MenuAction]:
        return self.get_menu_actions()

    def get_menu_actions(self) -> list[MenuAction]:
        return [
            MenuAction(key="search", title="搜索消息", description="按关键词检索历史消息"),
            MenuAction(key="export", title="导出会话 Markdown", description="导出完整会话到用户目录"),
            MenuAction(key="stats", title="查看 token 统计", description="查看当前会话 token 统计"),
        ]

    def _build_snippet(self, content: str, keyword: str, width: int = 24) -> str:
        lower = content.lower()
        idx = lower.find(keyword.lower())
        if idx < 0:
            return content[:width]
        start = max(0, idx - width // 2)
        end = min(len(content), idx + len(keyword) + width // 2)
        return content[start:end].replace("\n", " ")

    def _render_session_markdown(self, session: Session, messages: list[Any]) -> str:
        lines = [f"# {session.title}", "", f"- 会话 ID: `{session.id}`", f"- 用户 ID: `{session.user_id}`", f"- 创建时间: {session.created_at.isoformat()}", f"- 更新时间: {session.updated_at.isoformat()}", "", "## 消息记录", ""]
        for message in messages:
            lines.extend([f"### {message.sequence}. {message.role.value}", "", message.content, ""])
        lines.extend(["## 统计", "", f"- 消息总数: {len(messages)}", f"- 导出时间: {datetime.now(timezone.utc).isoformat()}"])
        return "\n".join(lines) + "\n"
