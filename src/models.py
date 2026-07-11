from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class TimeStampedModel(BaseModel):
    """所有业务模型共用的时间字段基类。"""

    # 统一用 UTC 时间，避免不同操作系统、容器和部署地域之间出现时区歧义。
    # 业务层如果需要本地化展示，可以在 UI 或接口层再转换为目标时区。

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="创建时间，统一使用 UTC 时间，避免跨时区存储和展示产生歧义。",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="最后更新时间，默认与创建时间一致，后续更新时由业务层刷新。",
    )

    @field_validator("created_at", "updated_at")
    @classmethod
    def _ensure_timezone_aware(cls, value: datetime) -> datetime:
        """强制时间字段带时区，避免 naive datetime 混入数据契约。"""

        # 只要时间值没有明确时区，就无法稳定地序列化、比较和跨系统同步。
        # 因此这里直接拒绝，避免“看似能用、实际会漂移”的隐性问题。

        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("时间字段必须是带时区的 datetime")
        return value.astimezone(timezone.utc)


class UserRole(str, Enum):
    """用户角色枚举。"""

    USER = "user"
    ADMIN = "admin"
    SYSTEM = "system"


class MessageRole(str, Enum):
    """消息发送方角色枚举。"""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class PresetScope(str, Enum):
    """预设可见范围。"""

    PRIVATE = "private"
    SHARED = "shared"
    GLOBAL = "global"


class User(TimeStampedModel):
    """系统用户的统一数据契约。"""

    id: UUID = Field(default_factory=uuid4, description="用户唯一标识。")
    username: str = Field(min_length=3, max_length=32, description="登录名或展示名。")
    email: str = Field(min_length=5, max_length=254, description="用户邮箱地址。")
    role: UserRole = Field(default=UserRole.USER, description="用户在系统中的权限角色。")
    is_active: bool = Field(default=True, description="是否启用。")

    @field_validator("username")
    @classmethod
    def _normalize_username(cls, value: str) -> str:
        # 统一去除首尾空格，避免同一个用户名因为输入习惯不同而出现“看起来相同、实际上不同”的脏数据。
        value = value.strip()
        if not value:
            raise ValueError("username 不能为空")
        return value

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        # 邮箱统一转小写是为了减少重复账号、唯一索引冲突和检索不一致的问题。
        value = value.strip().lower()
        if "@" not in value:
            raise ValueError("email 格式不合法")
        return value


class Session(TimeStampedModel):
    """会话数据契约，用于串联一次对话上下文。"""

    id: UUID = Field(default_factory=uuid4, description="会话唯一标识。")
    user_id: UUID = Field(description="所属用户 ID。")
    title: str = Field(default="新对话", min_length=1, max_length=120, description="会话标题。")
    is_archived: bool = Field(default=False, description="是否归档。")


class Message(TimeStampedModel):
    """消息数据契约，承载对话中的单条消息。"""

    id: UUID = Field(default_factory=uuid4, description="消息唯一标识。")
    session_id: UUID = Field(description="所属会话 ID。")
    role: MessageRole = Field(description="消息发送方角色。")
    content: str = Field(min_length=1, description="消息正文。")
    sequence: int = Field(ge=0, description="会话内顺序号，用于稳定排序。")
    metadata: dict[str, str] = Field(default_factory=dict, description="扩展元数据。")

    @field_validator("content")
    @classmethod
    def _strip_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content 不能为空")
        return value


class Preset(TimeStampedModel):
    """提示词/参数预设的数据契约。"""

    id: UUID = Field(default_factory=uuid4, description="预设唯一标识。")
    owner_id: UUID | None = Field(default=None, description="创建者 ID；全局预设可为空。")
    name: str = Field(min_length=1, max_length=80, description="预设名称。")
    scope: PresetScope = Field(default=PresetScope.PRIVATE, description="预设可见范围。")
    prompt_template: str = Field(min_length=1, description="提示词模板。")
    model_name: str = Field(min_length=1, max_length=128, description="关联模型名称。")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="采样温度。")

    @field_validator("name", "prompt_template", "model_name")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("文本字段不能为空")
        return value

    @model_validator(mode="after")
    def _validate_scope_and_owner(self) -> "Preset":
        """根据可见范围约束 owner_id 是否允许为空。"""

        # private/shared 预设必须明确归属某个用户，只有 global 预设才允许由系统侧统一管理。

        if self.scope in {PresetScope.PRIVATE, PresetScope.SHARED} and self.owner_id is None:
            raise ValueError("private/shared 作用域的 preset 必须绑定 owner_id")
        return self


class UserConfig(TimeStampedModel):
    """用户个性化配置的数据契约。"""

    id: UUID = Field(default_factory=uuid4, description="配置唯一标识。")
    user_id: UUID = Field(description="所属用户 ID。")
    active_preset_id: UUID | None = Field(default=None, description="当前启用的预设 ID。")
    theme: Literal["light", "dark", "system"] = Field(default="system", description="界面主题。")
    language: str = Field(default="zh-CN", min_length=2, max_length=20, description="界面语言。")
    default_model: str = Field(default="", description="默认模型名称。")
    preferences: dict[str, str] = Field(default_factory=dict, description="额外用户偏好设置。")

    @field_validator("language", "default_model")
    @classmethod
    def _strip_optional_text(cls, value: str) -> str:
        return value.strip()
