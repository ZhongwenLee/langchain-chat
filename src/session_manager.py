from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from .models import Message, MessageRole, Session
from .storage import StorageBackend, StoragePagination
from .user_manager import UserManager, UserManagerError


class SessionManagerError(RuntimeError):
    """会话管理模块的统一异常。"""


@dataclass(frozen=True)
class SessionSummary:
    """用于会话列表展示的精简结构。"""

    id: UUID
    user_id: UUID
    title: str
    message_count: int
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime
    is_archived: bool


@dataclass
class SessionManager:
    """会话管理器。

    这个类负责把“会话头信息”和“会话消息记录”组织成一个完整的业务单元，
    使上层可以直接完成新建、加载、列表、重命名、删除和自动保存等操作。

    设计上它依赖 `UserManager` 做用户归属校验，避免出现“跨用户读写会话”的越权问题。
    """

    storage: StorageBackend
    user_manager: UserManager
    active_session_id: UUID | None = field(default=None, init=False)

    def create_session(
        self,
        user_id: str | UUID | None = None,
        *,
        title: str | None = None,
        preset_hint: str | None = None,
        auto_select: bool = True,
    ) -> Session:
        """创建一个新会话。

        如果未显式传入标题，则会使用一个更友好的默认标题；如果提供了预设线索，
        则标题生成时会尽量把这个线索纳入，方便用户从会话列表里快速识别对话主题。
        """

        owner = self._resolve_user(user_id)
        session_title = self._normalize_title(title) if title else self._build_initial_title(preset_hint)
        session = Session.model_validate({"user_id": owner.id, "title": session_title})
        self.storage.create("sessions", session)
        if auto_select:
            # 新建会话后立即切换为当前活跃会话，能让上层的聊天界面少一次显式设置。
            self.active_session_id = session.id
        return session

    def load_session(self, session_id: str | UUID, user_id: str | UUID | None = None) -> Session:
        """加载历史会话，并校验其归属。"""

        session = self.get_session(session_id)
        if session is None:
            raise SessionManagerError(f"未找到会话: {session_id}")
        owner = self._resolve_user(user_id, allow_current=True)
        if session.user_id != owner.id:
            raise SessionManagerError("无权访问其他用户的会话")
        self.active_session_id = session.id
        return session

    def get_session(self, session_id: str | UUID) -> Session | None:
        return self._get_model("sessions", session_id, Session)

    def list_sessions(
        self,
        user_id: str | UUID | None = None,
        *,
        include_archived: bool = True,
        page: int = 1,
        page_size: int = 100,
    ) -> list[SessionSummary]:
        """列出某个用户的会话列表。

        这里返回摘要而不是完整消息，主要是为了让列表页快速渲染；
        真正进入对话详情时，再调用 `load_session` + `list_messages` 即可。
        """

        owner = self._resolve_user(user_id, allow_current=True)
        filters: dict[str, Any] = {"user_id": str(owner.id)}
        if not include_archived:
            filters["is_archived"] = 0
        page_data = self.storage.list("sessions", StoragePagination(page=page, page_size=page_size), filters=filters)
        summaries: list[SessionSummary] = []
        for session in page_data.items:
            if isinstance(session, Session):
                summaries.append(self._build_summary(session))
        return summaries

    def _build_summary(self, session: Session) -> SessionSummary:
        messages = self.list_messages(session.id, session.user_id)
        last_message_at = messages[-1].created_at if messages else None
        return SessionSummary(
            id=session.id,
            user_id=session.user_id,
            title=session.title,
            message_count=len(messages),
            last_message_at=last_message_at,
            created_at=session.created_at,
            updated_at=session.updated_at,
            is_archived=session.is_archived,
        )

    def rename_session(self, session_id: str | UUID, title: str, user_id: str | UUID | None = None) -> Session:
        """重命名会话。"""

        session = self._require_owned_session(session_id, user_id)
        renamed = Session.model_validate({**session.model_dump(), "title": self._normalize_title(title), "updated_at": datetime.now(timezone.utc)})
        self.storage.update("sessions", str(session.id), renamed)
        return renamed

    def delete_session(self, session_id: str | UUID, user_id: str | UUID | None = None) -> bool:
        """删除会话；由于消息表设置了级联删除，因此消息会同步清理。"""

        session = self._require_owned_session(session_id, user_id)
        deleted = self.storage.delete("sessions", str(session.id))
        if deleted and self.active_session_id == session.id:
            self.active_session_id = None
        return deleted

    def archive_session(self, session_id: str | UUID, user_id: str | UUID | None = None) -> Session:
        """将会话归档，便于把历史对话从默认列表中隐藏。"""

        session = self._require_owned_session(session_id, user_id)
        archived = Session.model_validate({**session.model_dump(), "is_archived": True, "updated_at": datetime.now(timezone.utc)})
        self.storage.update("sessions", str(session.id), archived)
        return archived

    def add_message(
        self,
        session_id: str | UUID,
        role: MessageRole | str,
        content: str,
        *,
        metadata: dict[str, str] | None = None,
        user_id: str | UUID | None = None,
        auto_title: bool = True,
    ) -> Message:
        """向会话追加消息，并自动持久化。

        这是“会话自动保存”的核心：调用方只要提交一次消息，这里就会负责：
        1. 校验会话归属；
        2. 计算消息序号；
        3. 写入消息表；
        4. 刷新会话更新时间；
        5. 在满足条件时自动生成标题。
        """

        session = self._require_owned_session(session_id, user_id)
        normalized_role = role if isinstance(role, MessageRole) else MessageRole(role)
        sequence = self._next_sequence(session.id)
        message = Message.model_validate(
            {
                "session_id": session.id,
                "role": normalized_role,
                "content": content,
                "sequence": sequence,
                "metadata": metadata or {},
            }
        )
        self.storage.create("messages", message)
        self._touch_session(session, auto_title=auto_title, trigger_message=message)
        return message

    def list_messages(self, session_id: str | UUID, user_id: str | UUID | None = None) -> list[Message]:
        """读取会话中的全部消息，按照 sequence 稳定排序。"""

        session = self._require_owned_session(session_id, user_id)
        page = self.storage.list("messages", StoragePagination(page=1, page_size=10000), filters={"session_id": str(session.id)})
        messages = [item for item in page.items if isinstance(item, Message)]
        return sorted(messages, key=lambda item: item.sequence)

    def get_active_session(self) -> Session | None:
        """读取当前活跃会话。"""

        if self.active_session_id is None:
            return None
        return self.get_session(self.active_session_id)

    def set_active_session(self, session_id: str | UUID, user_id: str | UUID | None = None) -> Session:
        """把某个会话设为当前活跃会话。"""

        session = self.load_session(session_id, user_id=user_id)
        self.active_session_id = session.id
        return session

    def _resolve_user(self, user_id: str | UUID | None, *, allow_current: bool = True):
        if user_id is None:
            if not allow_current:
                raise SessionManagerError("必须提供 user_id")
            current = self.user_manager.get_current_user()
            if current is None:
                raise SessionManagerError("当前没有可用用户")
            return current
        user = self.user_manager.get_user(user_id)
        if user is None:
            raise SessionManagerError(f"未找到用户: {user_id}")
        return user

    def _require_owned_session(self, session_id: str | UUID, user_id: str | UUID | None = None) -> Session:
        session = self.get_session(session_id)
        if session is None:
            raise SessionManagerError(f"未找到会话: {session_id}")
        owner = self._resolve_user(user_id)
        if session.user_id != owner.id:
            raise SessionManagerError("无权操作其他用户的会话")
        return session

    def _get_model(self, collection: str, item_id: str | UUID, model_type: type[BaseModel]) -> BaseModel | None:
        item = self.storage.get(collection, str(item_id))
        return item if isinstance(item, model_type) else None

    def _next_sequence(self, session_id: UUID) -> int:
        messages = self.list_messages(session_id)
        return (messages[-1].sequence + 1) if messages else 0

    def _touch_session(self, session: Session, *, auto_title: bool, trigger_message: Message) -> None:
        updated = Session.model_validate({**session.model_dump(), "updated_at": datetime.now(timezone.utc)})
        if auto_title:
            maybe_title = self._generate_title_if_needed(updated, trigger_message)
            if maybe_title is not None and maybe_title != updated.title:
                updated = Session.model_validate({**updated.model_dump(), "title": maybe_title, "updated_at": datetime.now(timezone.utc)})
        self.storage.update("sessions", str(session.id), updated)

    def _generate_title_if_needed(self, session: Session, trigger_message: Message) -> str | None:
        # 自动标题生成只在“标题仍然像默认占位”时触发，避免覆盖用户手工命名的会话。
        # 这种策略对 UX 更安全：系统只在最该介入的时机介入。
        if session.title.strip() != "新对话":
            return None
        if trigger_message.role not in {MessageRole.USER, MessageRole.SYSTEM}:
            return None
        preview = self._title_preview(trigger_message.content)
        return preview or "新对话"

    def _build_initial_title(self, preset_hint: str | None) -> str:
        if preset_hint:
            normalized = self._normalize_title(preset_hint)
            return f"{normalized}"
        return "新对话"

    def _normalize_title(self, title: str) -> str:
        normalized = title.strip()
        if not normalized:
            raise SessionManagerError("会话标题不能为空")
        return normalized[:120]

    def _title_preview(self, content: str) -> str:
        # 自动标题尽量简短，避免在会话列表里显示过长文本。
        # 这里按需求保留用户首条消息前 30 个字符作为标题预览，便于列表页快速识别主题。
        text = " ".join(content.strip().split())
        if len(text) <= 30:
            return text
        return f"{text[:30]}..."
