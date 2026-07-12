from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from .models import Session, User
from .session_manager import SessionSummary
from .chat_engine import ChatResponse


class UIKind(str, Enum):
    """UI 类型标识。

    这个枚举的目的不是区分“页面长什么样”，而是区分“由谁来承载交互”。
    例如命令行界面、未来的 WebUI、桌面 UI，都可以复用同一套业务适配协议。
    """

    TUI = "tui"
    WEB = "web"


@dataclass(frozen=True)
class UIState:
    """界面层共享状态。

    这里放的是 UI 渲染真正需要的最小信息，而不是把整个业务对象无脑塞进来。
    这样做的好处是：
    1. UI 可以快速渲染；
    2. 不同前端可以各取所需；
    3. 后续 WebUI、TUI、自动化测试都可以复用同一份状态结构。
    """

    kind: UIKind
    current_user: User | None = None
    active_session: Session | None = None
    sessions: list[SessionSummary] = field(default_factory=list)
    status_message: str = ""
    error_message: str = ""
    pending_input: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MenuAction:
    """菜单动作的统一描述。"""

    key: str
    title: str
    description: str = ""
    destructive: bool = False


@dataclass(frozen=True)
class UIEvent:
    """界面层向控制层发送的事件。"""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UIResult:
    """业务层返回给 UI 的统一结果。"""

    ok: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConversationPreview:
    """对话预览，供列表页或侧边栏使用。"""

    user_text: str
    assistant_text: str = ""
    response: ChatResponse | None = None


@runtime_checkable
class UIAdapter(Protocol):
    """统一 UI 适配协议。

    这个协议是本阶段最重要的“边界”定义：业务层不应该依赖具体是 TUI 还是 WebUI，
    只能依赖一个通用的 UI 适配器。
    """

    kind: UIKind

    def render_state(self, state: UIState) -> None: ...

    def render_message(self, message: str) -> None: ...

    def render_error(self, message: str) -> None: ...

    def render_menu(self, title: str, actions: list[MenuAction]) -> None: ...

    def ask_text(self, prompt: str, *, default: str | None = None) -> str: ...

    def ask_choice(self, prompt: str, options: list[MenuAction], *, allow_empty: bool = False) -> MenuAction | None: ...

    def pause(self, message: str = "按回车继续...") -> None: ...


@runtime_checkable
class UIService(Protocol):
    """UI 可调用的业务服务协议。

    这层负责把具体业务能力暴露给界面层，但不暴露底层存储细节。
    未来无论接入 TUI 还是 WebUI，都可以共享同一套服务接口。
    """

    def build_state(self) -> UIState: ...

    def refresh_state(self) -> UIState: ...

    def list_sessions(self) -> list[SessionSummary]: ...

    def list_users(self) -> list[User]: ...

    def switch_user(self, user_id: str | UUID) -> UIResult: ...

    def create_session(self, title: str | None = None) -> UIResult: ...

    def select_session(self, session_id: str | UUID) -> UIResult: ...

    def rename_session(self, session_id: str | UUID, title: str) -> UIResult: ...

    def send_message(self, content: str, *, session_id: str | UUID | None = None) -> UIResult: ...

    def get_preview(self, session_id: str | UUID | None = None) -> ConversationPreview | None: ...

    def handle_event(self, event: UIEvent) -> UIResult: ...
