from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.shortcuts import PromptSession
from prompt_toolkit.styles import Style

try:
    from rich.align import Align
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ModuleNotFoundError:
    class Text:
        def __init__(self, value: str = "", style: str | None = None):
            self._parts = [value]

        def append(self, text: str, style: str | None = None) -> None:
            self._parts.append(text)

        @classmethod
        def assemble(cls, *parts):
            value = "".join(str(part[1]) if isinstance(part, tuple) else str(part) for part in parts)
            return cls(value)

        def __str__(self) -> str:
            return "".join(self._parts)

    class Align:
        @staticmethod
        def left(value):
            return value

    class Group:
        def __init__(self, *items):
            self.items = list(items)

        def __iter__(self):
            return iter(self.items)

    class Panel:
        def __init__(self, renderable, title: str | None = None, border_style: str | None = None):
            self.renderable = renderable
            self.title = title
            self.border_style = border_style

    class Table:
        def __init__(self, title: str | None = None, expand: bool = False, show_lines: bool = False):
            self.title = title
            self.rows = []

        def add_column(self, *args, **kwargs):
            return None

        def add_row(self, *args):
            self.rows.append(args)

    class Console:
        def print(self, *args, **kwargs):
            return None

    class Live:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def update(self, *args, **kwargs):
            return None

from .chat_engine import ChatEngine, ChatResponse, TokenUsage
from .models import Session, User
from .session_manager import SessionManager, SessionManagerError, SessionSummary
from .ui_protocol import ConversationPreview, MenuAction, UIAdapter, UIEvent, UIKind, UIResult, UIService, UIState
from .user_manager import UserManager, UserManagerError


@dataclass
class TUIApp(UIAdapter, UIService):
    """命令行界面的主应用框架。"""

    chat_engine: ChatEngine
    session_manager: SessionManager
    user_manager: UserManager
    kind: UIKind = field(default=UIKind.TUI, init=False)
    state: UIState = field(default_factory=lambda: UIState(kind=UIKind.TUI), init=False)
    _last_preview: ConversationPreview | None = field(default=None, init=False)
    _paused: bool = field(default=False, init=False)
    _console: Console = field(default_factory=Console, init=False)
    _history: InMemoryHistory = field(default_factory=InMemoryHistory, init=False)

    def build_state(self) -> UIState:
        # UI 状态是 TUI 渲染的唯一数据源；这里把“当前用户、活跃会话、统计信息”一次性整理好。
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
                "paused": self._paused,
                "model_name": self.chat_engine.model.model_name,
            },
        )
        return self.state

    def refresh_state(self) -> UIState:
        # 刷新本质上就是重新构建状态，保证终端里展示的内容和业务层同步。
        return self.build_state()

    def render_state(self, state: UIState) -> None:
        self.state = state
        self._console.print(self._build_dashboard(state))

    def render_message(self, message: str) -> None:
        self._console.print(Panel(Text(message), border_style="green"))

    def render_error(self, message: str) -> None:
        self._console.print(Panel(Text(message, style="bold red"), border_style="red"))

    def render_menu(self, title: str, actions: list[MenuAction]) -> None:
        # 菜单只负责给用户一个“当前可执行动作”的清单，不在这里承载复杂业务逻辑。
        table = Table(title=title, expand=True, show_lines=False)
        table.add_column("快捷键", style="cyan", width=10)
        table.add_column("操作", style="white")
        for index, action in enumerate(actions, start=1):
            suffix = " [危险操作]" if action.destructive else ""
            table.add_row(str(index), f"{action.title}{suffix}")
        table.add_row("Enter", "发送消息 / 继续对话")
        table.add_row("/pause", "暂停自动刷新")
        table.add_row("/resume", "恢复自动刷新")
        table.add_row("/quit", "退出程序")
        self._console.print(table)

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
            self._last_preview = ConversationPreview(user_text=f"切换用户 {user.username}")
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

    async def stream_message(self, content: str, *, session_id: str | UUID | None = None) -> UIResult:
        """向模型发送消息并实时渲染增量回复。"""

        try:
            current_user = self.user_manager.get_current_user()
            user_id = current_user.id if current_user else None
            assistant_text: list[str] = []
            latest_usage = TokenUsage()
            stream_final_response: ChatResponse | None = None

            # 先打印一个“回复中”的面板，再在流式更新里不断刷新，让用户明确知道系统正在工作。
            self._console.print(self._build_stream_header(content))
            # rich.Live 会持续刷新同一块区域，非常适合终端里的“边生成边展示”体验。
            # 这里把用户问题、助手累计回复和 token 统计放进同一个面板，避免屏幕内容来回跳动。
            with Live(self._build_stream_panel(content, ""), console=self._console, refresh_per_second=30, transient=False) as live:
                async for chunk in self.chat_engine.astream(content, session_id=session_id, user_id=user_id):
                    # 只把非最终 chunk 视作“增量片段”；最终 chunk 往往携带完整内容和汇总统计。
                    if chunk.is_final:
                        if chunk.content:
                            assistant_text = [chunk.content]
                        if chunk.usage is not None:
                            latest_usage = chunk.usage
                        live.update(self._build_stream_panel(content, "".join(assistant_text), latest_usage))
                        final_session = self.session_manager.get_active_session() or self.session_manager.create_session(user_id=user_id)
                        stream_final_response = ChatResponse(
                            content="".join(assistant_text),
                            usage=chunk.usage or latest_usage,
                            assistant_message=self.session_manager.add_message(
                                final_session.id,
                                role="assistant",
                                content="".join(assistant_text),
                                user_id=user_id,
                                auto_title=False,
                            ),
                            model_name=self.chat_engine.model.model_name,
                            metadata=chunk.metadata,
                        )
                        continue

                    if chunk.content:
                        assistant_text.append(chunk.content)
                    # token 统计通常只会在流结束或接近结束时给出；因此这里保留最新一次可用的统计。
                    if chunk.usage is not None:
                        latest_usage = chunk.usage
                    live.update(self._build_stream_panel(content, "".join(assistant_text), latest_usage))

            response_text = "".join(assistant_text)
            if stream_final_response is None:
                session_obj = self.session_manager.get_active_session() or self.session_manager.create_session(user_id=user_id)
                assistant_message = self.session_manager.add_message(
                    session_obj.id,
                    role="assistant",
                    content=response_text,
                    user_id=user_id,
                    auto_title=False,
                )
                stream_final_response = ChatResponse(
                    content=response_text,
                    usage=latest_usage,
                    assistant_message=assistant_message,
                    model_name=self.chat_engine.model.model_name,
                )
            self._last_preview = ConversationPreview(user_text=content, assistant_text=stream_final_response.content, response=stream_final_response)
            if session_id is not None:
                self._last_preview.session_id = session_id
            self.build_state()
            self.render_message(self._format_usage(stream_final_response.usage))
            return UIResult(ok=True, message=stream_final_response.content, data={"tokens": stream_final_response.usage.total_tokens, "model_name": stream_final_response.model_name})
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
        if event.name == "pause":
            # 暂停只是冻结“自动发起对话”的动作，不影响用户查看列表或切换会话。
            self._paused = True
            self.build_state()
            return UIResult(ok=True, message="已暂停自动刷新")
        if event.name == "resume":
            self._paused = False
            self.build_state()
            return UIResult(ok=True, message="已恢复自动刷新")
        return UIResult(ok=False, message=f"未知事件: {event.name}")

    def run(self) -> None:
        asyncio.run(self.arun())

    async def arun(self) -> None:
        """启动一个真正可交互的终端聊天界面。"""

        self.build_state()
        prompt_style = Style.from_dict({"prompt": "bold cyan", "hint": "ansiblue"})
        session = PromptSession(history=self._history, style=prompt_style)

        while True:
            # 每一轮循环都先重新拉取状态，确保会话数量、活跃会话和当前用户信息都是最新的。
            state = self.refresh_state()
            await run_in_terminal(lambda: self.render_state(state))
            await run_in_terminal(lambda: self.render_menu("主菜单", self._main_actions()))

            raw = await session.prompt_async("请输入消息，或输入 /help 查看命令: ")
            command = raw.strip()
            if not command:
                continue
            if command in {"/quit", "/exit"}:
                self.render_message("已退出。")
                break
            if command == "/help":
                self.render_message("支持 /pause、/resume、/sessions、/users、/new、/switch、/select、/rename、/quit")
                continue
            if command == "/pause":
                self.handle_event(UIEvent(name="pause"))
                self.render_message("已暂停")
                continue
            if command == "/resume":
                self.handle_event(UIEvent(name="resume"))
                self.render_message("已恢复")
                continue
            if command == "/sessions":
                self._render_sessions()
                continue
            if command == "/users":
                self._render_users()
                continue
            if command == "/new":
                result = self.create_session("新对话")
                self._render_result(result)
                continue
            if command == "/switch":
                await self._prompt_switch_user(session)
                continue
            if command == "/select":
                await self._prompt_select_session(session)
                continue
            if command == "/rename":
                await self._prompt_rename_session(session)
                continue

            if self._paused:
                self.render_message("当前已暂停，若要继续请先输入 /resume")
                continue

            current_session = self.state.active_session
            result = await self.stream_message(command, session_id=current_session.id if current_session else None)
            self._render_result(result)

    def get_status_summary(self) -> str:
        # 这个字符串主要给自检脚本和状态栏使用，要求“短、准、可快速扫描”。
        state = self.refresh_state()
        model_name = state.metadata.get("model_name", self.chat_engine.model.model_name)
        user_name = state.current_user.username if state.current_user else "未登录"
        session_name = state.active_session.title if state.active_session else "无活跃会话"
        return f"模型 {model_name} | 用户 {user_name} | 会话 {session_name}"

    def _main_actions(self) -> list[MenuAction]:
        return [
            MenuAction(key="chat", title="发送消息"),
            MenuAction(key="new_session", title="新建会话"),
            MenuAction(key="select_session", title="选择会话"),
            MenuAction(key="rename_session", title="重命名会话"),
            MenuAction(key="switch_user", title="切换用户"),
            MenuAction(key="refresh", title="刷新状态"),
        ]

    def _build_dashboard(self, state: UIState) -> Panel:
        # 仪表盘是 TUI 的第一视觉焦点：用户进来就能看到当前登录用户、活跃会话、模型和暂停状态。
        lines = Text()
        lines.append("LangChain Chat TUI\n", style="bold cyan")
        lines.append(f"状态: {state.status_message or '就绪'}\n")
        lines.append(f"模型: {state.metadata.get('model_name', self.chat_engine.model.model_name)}\n")
        lines.append(f"当前用户: {state.current_user.username if state.current_user else '未选择'}\n")
        lines.append(f"当前会话: {state.active_session.title if state.active_session else '无'}\n")
        lines.append(f"会话数: {state.metadata.get('session_count', 0)} | 用户数: {state.metadata.get('user_count', 0)}\n")
        lines.append(f"暂停状态: {'是' if state.metadata.get('paused') else '否'}")
        return Panel(Align.left(lines), title="对话状态", border_style="blue")

    def _build_stream_header(self, prompt: str) -> Panel:
        # 在真正开始流式输出前，先用单独面板提示“当前正在生成”，避免界面像是卡住了。
        return Panel(Text(prompt), title="正在生成回复", border_style="magenta")

    def _build_stream_panel(self, user_text: str, assistant_text: str, usage: TokenUsage | None = None) -> Panel:
        # 流式面板同时展示用户输入、助手增量回复和 token 统计，方便用户直接观察模型输出过程。
        body = Group(
            Text.assemble(("你：", "bold cyan"), user_text),
            Text.assemble(("助手：", "bold green"), assistant_text or "正在思考..."),
            Text(self._format_usage(usage) if usage is not None else "Token 统计：等待模型结束后汇总"),
        )
        return Panel(body, title="流式回复", border_style="green")

    def _format_usage(self, usage: TokenUsage | None) -> str:
        usage = usage or TokenUsage()
        return f"Token 统计：prompt={usage.prompt_tokens} completion={usage.completion_tokens} total={usage.total_tokens}"

    def _render_result(self, result: UIResult) -> None:
        if result.ok:
            self.render_message(result.message or "操作成功")
        else:
            self.render_error(result.message or "操作失败")

    def _render_sessions(self) -> None:
        table = Table(title="会话列表")
        table.add_column("ID", overflow="fold")
        table.add_column("标题")
        table.add_column("消息数", justify="right")
        for item in self.list_sessions():
            table.add_row(str(item.id), item.title, str(item.message_count))
        self._console.print(table)

    def _render_users(self) -> None:
        table = Table(title="用户列表")
        table.add_column("ID", overflow="fold")
        table.add_column("用户名")
        table.add_column("邮箱", overflow="fold")
        for user in self.list_users():
            table.add_row(str(user.id), user.username, user.email)
        self._console.print(table)

    async def _prompt_switch_user(self, session: PromptSession[str]) -> None:
        users = self.list_users()
        if not users:
            self.render_error("暂无用户")
            return
        self._render_users()
        user_id = await session.prompt_async("输入要切换的用户 ID: ")
        result = self.switch_user(user_id.strip())
        self._render_result(result)

    async def _prompt_select_session(self, session: PromptSession[str]) -> None:
        sessions = self.list_sessions()
        if not sessions:
            self.render_error("暂无可选会话")
            return
        self._render_sessions()
        session_id = await session.prompt_async("输入要选择的会话 ID: ")
        result = self.select_session(session_id.strip())
        self._render_result(result)

    async def _prompt_rename_session(self, session: PromptSession[str]) -> None:
        active = self.state.active_session
        if active is None:
            self.render_error("当前没有活跃会话")
            return
        title = await session.prompt_async(f"新的会话标题 [{active.title}]: ")
        result = self.rename_session(active.id, title.strip() or active.title)
        self._render_result(result)
