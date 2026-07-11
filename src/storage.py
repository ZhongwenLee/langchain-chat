from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel

from .config_manager import ConfigBundle

TModel = TypeVar("TModel", bound=BaseModel)
TFilter = TypeVar("TFilter", covariant=True)


class StorageBackendType(str, Enum):
    """统一存储后端类型。"""

    SQLITE = "sqlite"
    MYSQL = "mysql"
    FILE = "file"


@dataclass(frozen=True)
class StoragePagination:
    """分页请求参数。"""

    page: int = 1
    page_size: int = 20

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError("page must be greater than or equal to 1")
        if self.page_size < 1:
            raise ValueError("page_size must be greater than or equal to 1")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


@dataclass(frozen=True)
class StoragePage(Generic[TModel]):
    """分页结果。"""

    items: list[TModel]
    total: int
    page: int
    page_size: int

    @property
    def has_next(self) -> bool:
        return self.page * self.page_size < self.total

    @property
    def has_previous(self) -> bool:
        return self.page > 1


@dataclass(frozen=True)
class StorageSearchQuery:
    """搜索查询的统一结构。"""

    keyword: str
    fields: tuple[str, ...] = ()
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True)
class StorageExportResult:
    """导出结果的统一结构。"""

    payload: str | bytes
    format: str
    content_type: str | None = None
    filename: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class StorageBackend(ABC):
    """所有存储后端的统一抽象基类。"""

    backend_type: StorageBackendType

    @abstractmethod
    def connect(self) -> None:
        """初始化底层资源。"""

    @abstractmethod
    def close(self) -> None:
        """释放底层资源。"""

    @abstractmethod
    def begin(self) -> None:
        """开启事务。"""

    @abstractmethod
    def commit(self) -> None:
        """提交事务。"""

    @abstractmethod
    def rollback(self) -> None:
        """回滚事务。"""

    @abstractmethod
    def create(self, collection: str, item: BaseModel) -> BaseModel:
        """创建单条记录。"""

    @abstractmethod
    def get(self, collection: str, item_id: str) -> BaseModel | None:
        """按主键读取单条记录。"""

    @abstractmethod
    def update(self, collection: str, item_id: str, item: BaseModel) -> BaseModel:
        """更新单条记录。"""

    @abstractmethod
    def delete(self, collection: str, item_id: str) -> bool:
        """删除单条记录。"""

    @abstractmethod
    def list(
        self,
        collection: str,
        pagination: StoragePagination | None = None,
        filters: dict[str, Any] | None = None,
    ) -> StoragePage[BaseModel]:
        """分页列出记录。"""

    @abstractmethod
    def search(self, collection: str, query: StorageSearchQuery) -> StoragePage[BaseModel]:
        """执行关键词搜索。"""

    @abstractmethod
    def export(self, collection: str, format: str = "json") -> StorageExportResult:
        """导出某个集合的数据。"""


class StorageFactory:
    """根据配置创建具体存储后端。"""

    def create(self, config: ConfigBundle) -> StorageBackend:
        database_url = config.secrets.database_url.strip()
        backend_type = self._resolve_backend_type(database_url)
        backend_config = self._build_backend_config(config, backend_type, database_url)
        return backend_config

    def _resolve_backend_type(self, database_url: str) -> StorageBackendType:
        scheme = database_url.split(":", 1)[0].lower()
        if scheme in {"sqlite", "sqlite3"}:
            return StorageBackendType.SQLITE
        if scheme in {"mysql", "mysql+pymysql", "mysql+mysqldb", "mysql+mysqlconnector"}:
            return StorageBackendType.MYSQL
        if scheme == "file":
            return StorageBackendType.FILE
        raise ValueError(f"不支持的存储后端: {database_url}")

    def _build_backend_config(
        self,
        config: ConfigBundle,
        backend_type: StorageBackendType,
        database_url: str,
    ) -> StorageBackend:
        if backend_type is StorageBackendType.SQLITE:
            return SQLiteStorageBackend(database_url=database_url, config=config)
        if backend_type is StorageBackendType.MYSQL:
            return MySQLStorageBackend(database_url=database_url, config=config)
        return FileStorageBackend(base_path=Path(database_url.removeprefix("file://")), config=config)


@dataclass
class SQLiteStorageBackend(StorageBackend):
    database_url: str
    config: ConfigBundle
    backend_type: StorageBackendType = StorageBackendType.SQLITE

    def connect(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def begin(self) -> None:
        raise NotImplementedError

    def commit(self) -> None:
        raise NotImplementedError

    def rollback(self) -> None:
        raise NotImplementedError

    def create(self, collection: str, item: BaseModel) -> BaseModel:
        raise NotImplementedError

    def get(self, collection: str, item_id: str) -> BaseModel | None:
        raise NotImplementedError

    def update(self, collection: str, item_id: str, item: BaseModel) -> BaseModel:
        raise NotImplementedError

    def delete(self, collection: str, item_id: str) -> bool:
        raise NotImplementedError

    def list(
        self,
        collection: str,
        pagination: StoragePagination | None = None,
        filters: dict[str, Any] | None = None,
    ) -> StoragePage[BaseModel]:
        raise NotImplementedError

    def search(self, collection: str, query: StorageSearchQuery) -> StoragePage[BaseModel]:
        raise NotImplementedError

    def export(self, collection: str, format: str = "json") -> StorageExportResult:
        raise NotImplementedError


@dataclass
class MySQLStorageBackend(SQLiteStorageBackend):
    backend_type: StorageBackendType = StorageBackendType.MYSQL


@dataclass
class FileStorageBackend(StorageBackend):
    base_path: Path
    config: ConfigBundle
    backend_type: StorageBackendType = StorageBackendType.FILE

    def connect(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def begin(self) -> None:
        raise NotImplementedError

    def commit(self) -> None:
        raise NotImplementedError

    def rollback(self) -> None:
        raise NotImplementedError

    def create(self, collection: str, item: BaseModel) -> BaseModel:
        raise NotImplementedError

    def get(self, collection: str, item_id: str) -> BaseModel | None:
        raise NotImplementedError

    def update(self, collection: str, item_id: str, item: BaseModel) -> BaseModel:
        raise NotImplementedError

    def delete(self, collection: str, item_id: str) -> bool:
        raise NotImplementedError

    def list(
        self,
        collection: str,
        pagination: StoragePagination | None = None,
        filters: dict[str, Any] | None = None,
    ) -> StoragePage[BaseModel]:
        raise NotImplementedError

    def search(self, collection: str, query: StorageSearchQuery) -> StoragePage[BaseModel]:
        raise NotImplementedError

    def export(self, collection: str, format: str = "json") -> StorageExportResult:
        raise NotImplementedError
