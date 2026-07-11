from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from .models import User, UserConfig
from .storage import StorageBackend, StoragePagination


class UserManagerError(RuntimeError):
    """用户管理模块的统一异常。"""


@dataclass(frozen=True)
class UserPreferenceChange:
    """用户偏好变更结果。"""

    user_id: UUID
    preferences: dict[str, str]


@dataclass
class UserManager:
    """多用户管理器。

    这个类把“用户账号”“当前登录用户”“用户配置”三件事串起来，
    作为后续会话隔离、个性化设置和数据归属校验的基础设施。
    """

    storage: StorageBackend
    current_user_id: UUID | None = field(default=None, init=False)

    def create_user(
        self,
        username: str,
        email: str,
        *,
        is_active: bool = True,
        role: str = "user",
        theme: str = "system",
        language: str = "zh-CN",
        default_model: str = "",
        preferences: dict[str, str] | None = None,
    ) -> User:
        """创建用户，并同时初始化该用户的偏好配置。"""

        normalized_username = username.strip()
        normalized_email = email.strip().lower()
        self._ensure_username_unique(normalized_username)
        self._ensure_email_unique(normalized_email)

        user = User.model_validate(
            {
                "username": normalized_username,
                "email": normalized_email,
                "role": role,
                "is_active": is_active,
            }
        )
        self.storage.create("users", user)
        self._ensure_user_config(
            user,
            theme=theme,
            language=language,
            default_model=default_model,
            preferences=preferences or {},
        )
        if self.current_user_id is None:
            # 第一个创建出来的用户默认成为“当前登录用户”，
            # 这样可以让初始化阶段的调用方更少做一次额外切换。
            self.current_user_id = user.id
        return user

    def switch_current_user(self, user_id: str | UUID) -> User:
        """切换当前登录用户。"""

        user = self.get_user(user_id)
        if user is None:
            raise UserManagerError(f"未找到用户: {user_id}")
        if not user.is_active:
            raise UserManagerError(f"用户已停用，无法切换: {user.username}")
        self.current_user_id = user.id
        self._ensure_user_config(user)
        return user

    def get_current_user(self) -> User | None:
        """读取当前登录用户。"""

        if self.current_user_id is None:
            return None
        return self.get_user(self.current_user_id)

    def delete_user(self, user_id: str | UUID) -> bool:
        """删除用户，并清理其关联配置与下游依赖。"""

        user = self.get_user(user_id)
        if user is None:
            return False
        deleted = self.storage.delete("users", str(user.id))
        if deleted and self.current_user_id == user.id:
            # 当前用户被删掉后，必须把登录态一并清空，
            # 否则后续访问会出现“当前用户已经不存在”的悬空状态。
            self.current_user_id = None
        return deleted

    def get_user(self, user_id: str | UUID) -> User | None:
        return self._get_model("users", user_id, User)

    def list_users(self) -> list[User]:
        page = self.storage.list("users", StoragePagination(page=1, page_size=1000))
        return [item for item in page.items if isinstance(item, User)]

    def get_user_config(self, user_id: str | UUID | None = None) -> UserConfig:
        """读取指定用户的配置；未传入时默认读取当前用户。"""

        target_user_id = self._resolve_user_id(user_id)
        config = self._get_user_config(target_user_id)
        if config is None:
            user = self.get_user(target_user_id)
            if user is None:
                raise UserManagerError(f"未找到用户: {target_user_id}")
            config = self._ensure_user_config(user)
        return config

    def update_user_preferences(self, preferences: dict[str, str], user_id: str | UUID | None = None) -> UserPreferenceChange:
        """写入用户偏好，采用“合并更新”而不是整块覆盖。"""

        config = self.get_user_config(user_id)
        merged_preferences = {**config.preferences, **preferences}
        updated = UserConfig.model_validate(
            {
                **config.model_dump(),
                "preferences": merged_preferences,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self.storage.update("user_configs", str(updated.id), updated)
        return UserPreferenceChange(user_id=updated.user_id, preferences=updated.preferences)

    def set_user_preference(self, key: str, value: str, user_id: str | UUID | None = None) -> UserPreferenceChange:
        """设置单个偏好项。

        这个方法是对 `update_user_preferences` 的小封装，方便调用方在 UI 或接口层按键值更新。
        """

        return self.update_user_preferences({key: value}, user_id=user_id)

    def _ensure_username_unique(self, username: str) -> None:
        for user in self.list_users():
            if user.username == username:
                raise UserManagerError(f"用户名已存在: {username}")

    def _ensure_email_unique(self, email: str) -> None:
        for user in self.list_users():
            if user.email == email:
                raise UserManagerError(f"邮箱已存在: {email}")

    def _ensure_user_config(
        self,
        user: User,
        *,
        theme: str = "system",
        language: str = "zh-CN",
        default_model: str = "",
        preferences: dict[str, str] | None = None,
    ) -> UserConfig:
        existing = self._get_user_config(user.id)
        if existing is not None:
            return existing
        config = UserConfig.model_validate(
            {
                "user_id": user.id,
                "theme": theme,
                "language": language,
                "default_model": default_model,
                "preferences": preferences or {},
            }
        )
        self.storage.create("user_configs", config)
        return config

    def _get_user_config(self, user_id: UUID) -> UserConfig | None:
        page = self.storage.list("user_configs", StoragePagination(page=1, page_size=1000), filters={"user_id": str(user_id)})
        return next((item for item in page.items if isinstance(item, UserConfig)), None)

    def _get_model(self, collection: str, item_id: str | UUID, model_type: type[BaseModel]) -> BaseModel | None:
        item = self.storage.get(collection, str(item_id))
        return item if isinstance(item, model_type) else None

    def _resolve_user_id(self, user_id: str | UUID | None) -> UUID:
        if user_id is None:
            if self.current_user_id is None:
                raise UserManagerError("当前没有登录用户")
            return self.current_user_id
        return UUID(str(user_id))
