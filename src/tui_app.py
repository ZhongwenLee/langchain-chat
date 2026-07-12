from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from .chat_engine import ChatEngine, ChatResponse
from .models import Session, User
from .session_manager import SessionManager, SessionManagerError, SessionSummary
from .ui_protocol import ConversationPreview, MenuAction, UIAdapter, UIEvent, UIKind, UIResult, UIService, UIState
from .user_manager import UserManager, UserManagerError


@dataclass
class TUIApp(UIAdapter, UIService):
    """命令行界面的主应用框架。

    这个类有两个职责：
    1. 作为 UI 适配器，负责把状态渲染成命令行可读的文本；
    2. 作为 UI 服务实现，负责把用户在菜单中的操作翻译成业务层调用。

    这样的分层可以让 WebUI 未来直接复用同一套 `UIService`，而无需重写业务编排逻辑。
    """

    chat_engine: ChatEngine
    session_manager: SessionManager
    user_manager: UserManager
    kind: UIKind = field(default=UIKind.TUI, init=False)
    state: UIState = field(default_factory=lambda: UIState(kind=UIKind.TUI), init=False)
    _last_preview: ConversationPreview | None = field(default=None, init=False)

    def build_state(self) -> UIState:
        current_user = self.user_manager.get_current_user()
        active_session = self.session_manager.get_active_session()
        sessions = self.session_manager.list_sessions(current_user.id if current_user else None)
        self.state = UIState(
            kind=self.kind,
            current_user=current_user,
            active_session=active_session,
            sessions=sessions,
            status_message="就绪" if current_user else "尚未登录用户",
            metadata={
                "session_count": len(sessions),
                "user_count": len(self.user_manager.list_users()),
            },
        )
        return self.state

    def refresh_state(self) -> UIState:
        return self.build_state()

    def render_state(self, state: UIState) -> None:
        self.state = state
        print("\n========== LangChain Chat TUI ==========")
        print(f"模式: {state.kind.value}")
        print(f"状态: {state.status_message or '就绪'}")
        if state.current_user:
            print(f"当前用户: {state.current_user.username} ({state.current_user.email})")
        else:
            print("当前用户: 未选择")
        if state.active_session:
            print(f"活跃会话: {state.active_session.title} [{state.active_session.id}]")
        else:
            print("活跃会话: 无")
        print(f"会话数量: {state.metadata.get('session_count', 0)}")
        print("======================================\n")

    def render_message(self, message: str) -> None:
        print(message)

    def render_error(self, message: str) -> None:
        print(f"[错误] {message}")

    def render_menu(self, title: str, actions: list[MenuAction]) -> None:
        print(f"\n{title}")
        for index, action in enumerate(actions, start=1):
            suffix = " [危险操作]" if action.destructive else ""
            print(f"{index}. {action.title}{suffix}")
        print("0. 退出")

    def ask_text(self, prompt: str, *, default: str | None = None) -> str:
        suffix = f" [{default}]" if default else ""
        value = input(f"{prompt}{suffix}: ").strip()
        return value or (default or "")

    def ask_choice(self, prompt: str, options: list[MenuAction], *, allow_empty: bool = False) -> MenuAction | None:
        while True:
            raw = input(f"{prompt}: ").strip()
            if allow_empty and raw == "":
                return None
            if raw == "0":
                return None
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(options):
                    return options[idx]
            print("请输入有效编号。")

    def pause(self, message: str = "按回车继续...") -> None:
        input(message)

    def list_sessions(self) -> list[SessionSummary]:
        current_user = self.user_manager.get_current_user()
        return self.session_manager.list_sessions(current_user.id if current_user else None)

    def list_users(self) -> list[User]:
        return self.user_manager.list_users()

    def switch_user(self, user_id: str | UUID) -> UIResult:
        try:
            user = self.user_manager.switch_current_user(user_id)
            self.build_state()
            return UIResult(ok=True, message=f"已切换到用户: {user.username}", data={"user_id": str(user.id)})
        except UserManagerError as exc:
            return UIResult(ok=False, message=str(exc))

    def create_session(self, title: str | None = None) -> UIResult:
        try:
            current_user = self.user_manager.get_current_user()
            session = self.session_manager.create_session(current_user.id if current_user else None, title=title)
            self.build_state()
            return UIResult(ok=True, message=f"已创建会话: {session.title}", data={"session_id": str(session.id)})
        except SessionManagerError as exc:
            return UIResult(ok=False, message=str(exc))

    def select_session(self, session_id: str | UUID) -> UIResult:
        try:
            current_user = self.user_manager.get_current_user()
            session = self.session_manager.set_active_session(session_id, user_id=current_user.id if current_user else None)
            self.build_state()
            return UIResult(ok=True, message=f"已选择会话: {session.title}", data={"session_id": str(session.id)})
        except SessionManagerError as exc:
            return UIResult(ok=False, message=str(exc))

    def rename_session(self, session_id: str | UUID, title: str) -> UIResult:
        try:
            current_user = self.user_manager.get_current_user()
            session = self.session_manager.rename_session(session_id, title, user_id=current_user.id if current_user else None)
            self.build_state()
            return UIResult(ok=True, message=f"已重命名为: {session.title}", data={"session_id": str(session.id)})
        except SessionManagerError as exc:
            return UIResult(ok=False, message=str(exc))

    async def send_message(self, content: str, *, session_id: str | UUID | None = None) -> UIResult:
        try:
            current_user = self.user_manager.get_current_user()
            response: ChatResponse = await self.chat_engine.ask(content, session_id=session_id, user_id=current_user.id if current_user else None)
            self._last_preview = ConversationPreview(user_text=content, assistant_text=response.content, response=response)
            self.build_state()
            return UIResult(ok=True, message=response.content, data={"tokens": response.usage.total_tokens, "model_name": response.model_name})
        except Exception as exc:
            return UIResult(ok=False, message=str(exc))

    def get_preview(self, session_id: str | UUID | None = None) -> ConversationPreview | None:
        if self._last_preview is not None:
            return self._last_preview
        if session_id is None:
            return None
        return ConversationPreview(user_text=f"会话 {session_id}")

    def handle_event(self, event: UIEvent) -> UIResult:
        if event.name == "refresh":
            self.refresh_state()
            return UIResult(ok=True, message="已刷新界面")
        if event.name == "switch_user":
            return self.switch_user(event.payload.get("user_id", ""))
        if event.name == "create_session":
            return self.create_session(event.payload.get("title"))
        if event.name == "select_session":
            return self.select_session(event.payload.get("session_id", ""))
        if event.name == "rename_session":
            return self.rename_session(event.payload.get("session_id", ""), event.payload.get("title", ""))
        return UIResult(ok=False, message=f"未知事件: {event.name}")

    def run(self) -> None:
        """启动一个最小可用的 TUI 主循环。"""

        self.build_state()
        while True:
            state = self.refresh_state()
            self.render_state(state)
            actions = [
                MenuAction(key="chat", title="发送消息"),
                MenuAction(key="new_session", title="新建会话"),
                MenuAction(key="select_session", title="选择会话"),
                MenuAction(key="rename_session", title="重命名会话"),
                MenuAction(key="switch_user", title="切换用户"),
                MenuAction(key="refresh", title="刷新状态"),
            ]
            self.render_menu("主菜单", actions)
            choice = self.ask_choice("请选择操作", actions, allow_empty=True)
            if choice is None:
                self.render_message("已退出。")
                break
            if choice.key == "chat":
                content = self.ask_text("请输入消息")
                if not content:
                    self.render_error("消息不能为空")
                    continue
                import asyncio

                result = asyncio.run(self.send_message(content, session_id=self.state.active_session.id if self.state.active_session else None))
                self.render_message(result.message)
            elif choice.key == "new_session":
                title = self.ask_text("请输入会话标题", default="新对话")
                result = self.create_session(title or None)
                self.render_message(result.message)
            elif choice.key == "select_session":
                sessions = self.list_sessions()
                if not sessions:
                    self.render_error("暂无可选会话")
                    continue
                session_actions = [MenuAction(key=str(item.id), title=f"{item.title}（{item.message_count} 条消息）") for item in sessions]
                selected = self.ask_choice("选择会话", session_actions)
                if selected is None:
                    continue
                result = self.select_session(selected.key)
                self.render_message(result.message)
            elif choice.key == "rename_session":
                active = self.state.active_session
                if active is None:
                    self.render_error("当前没有活跃会话")
                    continue
                title = self.ask_text("请输入新标题", default=active.title)
                result = self.rename_session(active.id, title)
                self.render_message(result.message)
            elif choice.key == "switch_user":
                users = self.list_users()
                if not users:
                    self.render_error("暂无用户")
                    continue
                user_actions = [MenuAction(key=str(user.id), title=user.username) for user in users]
                selected = self.ask_choice("选择用户", user_actions)
                if selected is None:
                    continue
                result = self.switch_user(selected.key)
                self.render_message(result.message)
            elif choice.key == "refresh":
                self.handle_event(UIEvent(name="refresh"))
                self.render_message("已刷新")
