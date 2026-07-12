from __future__ import annotations

import asyncio
import json
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Generic, TypeVar
from uuid import UUID

import aiosqlite
from pydantic import BaseModel

from .config_manager import ConfigBundle
from .models import Message, Preset, Session, User, UserConfig

TModel = TypeVar("TModel", bound=BaseModel)
TFilter = TypeVar("TFilter", covariant=True)


class StorageBackendType(str, Enum):
    """统一存储后端类型。"""

    SQLITE = "sqlite"
    MYSQL = "mysql"
    FILE = "file"


@dataclass(frozen=True)
class StoragePagination:
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
    keyword: str
    fields: tuple[str, ...] = ()
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True)
class StorageExportResult:
    payload: str | bytes
    format: str
    content_type: str | None = None
    filename: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class StorageBackend(ABC):
    backend_type: StorageBackendType

    @abstractmethod
    def connect(self) -> None: ...
    @abstractmethod
    def close(self) -> None: ...
    @abstractmethod
    def begin(self) -> None: ...
    @abstractmethod
    def commit(self) -> None: ...
    @abstractmethod
    def rollback(self) -> None: ...
    @abstractmethod
    def create(self, collection: str, item: BaseModel) -> BaseModel: ...
    @abstractmethod
    def get(self, collection: str, item_id: str) -> BaseModel | None: ...
    @abstractmethod
    def update(self, collection: str, item_id: str, item: BaseModel) -> BaseModel: ...
    @abstractmethod
    def delete(self, collection: str, item_id: str) -> bool: ...
    @abstractmethod
    def list(self, collection: str, pagination: StoragePagination | None = None, filters: dict[str, Any] | None = None) -> StoragePage[BaseModel]: ...
    @abstractmethod
    def search(self, collection: str, query: StorageSearchQuery) -> StoragePage[BaseModel]: ...
    @abstractmethod
    def export(self, collection: str, format: str = "json") -> StorageExportResult: ...


class StorageFactory:
    def create(self, config: ConfigBundle) -> StorageBackend:
        database_url = config.secrets.database_url.strip()
        backend_type = self._resolve_backend_type(database_url)
        return self._build_backend_config(config, backend_type, database_url)

    def create_backend(self, config: ConfigBundle) -> StorageBackend:
        """兼容更直观的工厂接口命名。"""

        return self.create(config)

    def _resolve_backend_type(self, database_url: str) -> StorageBackendType:
        scheme = database_url.split(":", 1)[0].lower()
        if scheme in {"sqlite", "sqlite3"}:
            return StorageBackendType.SQLITE
        if scheme in {"mysql", "mysql+pymysql", "mysql+mysqldb", "mysql+mysqlconnector"}:
            return StorageBackendType.MYSQL
        if scheme == "file":
            return StorageBackendType.FILE
        raise ValueError(f"不支持的存储后端: {database_url}")

    def _build_backend_config(self, config: ConfigBundle, backend_type: StorageBackendType, database_url: str) -> StorageBackend:
        if backend_type is StorageBackendType.SQLITE:
            return SQLiteStorageBackend(database_url=database_url, config=config)
        if backend_type is StorageBackendType.MYSQL:
            return MySQLStorageBackend(database_url=database_url, config=config)
        file_path = Path(database_url.removeprefix("file://"))
        if file_path.suffix != ".json":
            file_path = file_path / "storage"
        return FileStorageBackend(base_path=file_path, config=config)


@dataclass
class SQLiteStorageBackend(StorageBackend):
    database_url: str
    config: ConfigBundle
    backend_type: StorageBackendType = StorageBackendType.SQLITE
    _connection: aiosqlite.Connection | None = field(default=None, init=False, repr=False)

    def _db_path(self) -> str:
        # 兼容常见的 SQLite URL 写法：
        # - 内存库用于测试和临时初始化
        # - 三斜杠表示本地文件路径
        # - 兜底处理一些较宽松的 sqlite:// 前缀
        if self.database_url in {"sqlite://", "sqlite:///:memory:", "sqlite3:///:memory:"}:
            return ":memory:"
        if self.database_url.startswith("sqlite:///"):
            return self.database_url.removeprefix("sqlite:///")
        if self.database_url.startswith("sqlite3:///"):
            return self.database_url.removeprefix("sqlite3:///")
        return self.database_url.removeprefix("sqlite://")

    def _run(self, coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        # 在已经运行事件循环的上下文里，不能直接阻塞等待同一个 loop 的协程，
        # 因此把协程放到独立线程里执行，保留同步 API 的可用性。
        import threading

        result: dict[str, Any] = {}
        error: list[BaseException] = []

        def runner() -> None:
            try:
                result["value"] = asyncio.run(coro)
            except BaseException as exc:  # noqa: BLE001
                error.append(exc)

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if error:
            raise error[0]
        return result.get("value")

    async def ainitialize(self) -> None:
        await self.aconnect()

    async def aconnect(self) -> None:
        # 连接建立时立即执行建表，保证“默认可运行后端”开箱可用。
        if self._connection is not None:
            return
        path = self._db_path()
        if path not in {":memory:"}:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA foreign_keys = ON;")
        await self._connection.commit()
        await self._init_schema()

    async def aclose(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def initialize(self) -> None:
        await self.aconnect()

    async def abegin(self) -> None:
        await self._ensure_connection()
        await self._connection.execute("BEGIN;")

    async def acommit(self) -> None:
        await self._ensure_connection()
        await self._connection.commit()

    async def arollback(self) -> None:
        await self._ensure_connection()
        await self._connection.rollback()

    def connect(self) -> None: self._run(self.aconnect())
    def close(self) -> None: self._run(self.aclose())
    def begin(self) -> None: self._run(self.abegin())
    def commit(self) -> None: self._run(self.acommit())
    def rollback(self) -> None: self._run(self.arollback())
    def create(self, collection: str, item: BaseModel) -> BaseModel: return self._run(self.acreate(collection, item))
    def get(self, collection: str, item_id: str) -> BaseModel | None: return self._run(self.aget(collection, item_id))
    def update(self, collection: str, item_id: str, item: BaseModel) -> BaseModel: return self._run(self.aupdate(collection, item_id, item))
    def delete(self, collection: str, item_id: str) -> bool: return self._run(self.adelete(collection, item_id))
    def list(self, collection: str, pagination: StoragePagination | None = None, filters: dict[str, Any] | None = None) -> StoragePage[BaseModel]: return self._run(self.alist(collection, pagination, filters))
    def search(self, collection: str, query: StorageSearchQuery) -> StoragePage[BaseModel]: return self._run(self.asearch(collection, query))
    def export(self, collection: str, format: str = "json") -> StorageExportResult: return self._run(self.aexport(collection, format))

    async def _ensure_connection(self) -> None:
        if self._connection is None:
            await self.aconnect()

    async def _init_schema(self) -> None:
        # 这里的表结构是当前业务闭环的最小集合：用户、会话、消息、预设、用户配置。
        # 设计原则是字段尽量贴近模型定义，避免额外的 ORM 映射层带来复杂度。
        assert self._connection is not None
        await self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL,
                is_active INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                is_archived INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                metadata TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                UNIQUE(session_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS presets (
                id TEXT PRIMARY KEY,
                owner_id TEXT,
                name TEXT NOT NULL,
                scope TEXT NOT NULL,
                prompt_template TEXT NOT NULL,
                model_name TEXT NOT NULL,
                temperature REAL NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS user_configs (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL UNIQUE,
                active_preset_id TEXT,
                theme TEXT NOT NULL,
                language TEXT NOT NULL,
                default_model TEXT NOT NULL,
                preferences TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(active_preset_id) REFERENCES presets(id) ON DELETE SET NULL
            );
            """
        )
        await self._connection.commit()

    def _table_for(self, collection: str) -> str:
        mapping = {"users": "users", "sessions": "sessions", "messages": "messages", "presets": "presets", "user_configs": "user_configs"}
        if collection not in mapping:
            raise ValueError(f"不支持的集合: {collection}")
        return mapping[collection]

    def _serialize_model(self, item: BaseModel) -> dict[str, Any]:
        # SQLite 不直接理解 UUID、Enum、datetime 这类 Python 对象，
        # 因此在写入前统一压平为字符串/整数/JSON 文本，保证后续可逆读取。
        data = item.model_dump()
        for key, value in list(data.items()):
            if isinstance(value, UUID): data[key] = str(value)
            elif isinstance(value, datetime): data[key] = value.astimezone(timezone.utc).isoformat()
            elif isinstance(value, Enum): data[key] = value.value
            elif isinstance(value, dict): data[key] = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, bool): data[key] = int(value)
        return data

    def _deserialize_row(self, collection: str, row: sqlite3.Row) -> BaseModel:
        # 读取时做与写入相反的还原，让外部拿到的对象始终保持模型层的语义。
        data = dict(row)
        if collection == "messages":
            data["metadata"] = json.loads(data["metadata"] or "{}")
        elif collection == "user_configs":
            data["preferences"] = json.loads(data["preferences"] or "{}")
        model_map = {"users": User, "sessions": Session, "messages": Message, "presets": Preset, "user_configs": UserConfig}
        return model_map[collection].model_validate(data)

    async def acreate(self, collection: str, item: BaseModel) -> BaseModel:
        await self._ensure_connection()
        table = self._table_for(collection)
        data = self._serialize_model(item)
        columns = ", ".join(data.keys())
        placeholders = ", ".join([":" + key for key in data])
        await self._connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", data)
        await self._connection.commit()
        return item

    async def aget(self, collection: str, item_id: str) -> BaseModel | None:
        await self._ensure_connection()
        table = self._table_for(collection)
        cursor = await self._connection.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return None if row is None else self._deserialize_row(collection, row)

    async def aupdate(self, collection: str, item_id: str, item: BaseModel) -> BaseModel:
        await self._ensure_connection()
        table = self._table_for(collection)
        data = self._serialize_model(item)
        assignments = ", ".join(f"{key} = :{key}" for key in data if key != "id")
        data["id"] = item_id
        await self._connection.execute(f"UPDATE {table} SET {assignments} WHERE id = :id", data)
        await self._connection.commit()
        return item

    async def adelete(self, collection: str, item_id: str) -> bool:
        await self._ensure_connection()
        table = self._table_for(collection)
        cursor = await self._connection.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))
        await self._connection.commit()
        return cursor.rowcount > 0

    async def alist(self, collection: str, pagination: StoragePagination | None = None, filters: dict[str, Any] | None = None) -> StoragePage[BaseModel]:
        await self._ensure_connection()
        table = self._table_for(collection)
        pagination = pagination or StoragePagination()
        where, params = [], []
        if filters:
            for key, value in filters.items():
                where.append(f"{key} = ?")
                params.append(str(value) if isinstance(value, UUID) else value)
        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        total_cursor = await self._connection.execute(f"SELECT COUNT(*) FROM {table} {where_clause}", params)
        total = (await total_cursor.fetchone())[0]
        cursor = await self._connection.execute(f"SELECT * FROM {table} {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?", [*params, pagination.page_size, pagination.offset])
        rows = await cursor.fetchall()
        return StoragePage(items=[self._deserialize_row(collection, row) for row in rows], total=total, page=pagination.page, page_size=pagination.page_size)

    async def asearch(self, collection: str, query: StorageSearchQuery) -> StoragePage[BaseModel]:
        # 关键词搜索采用最朴素的 LIKE 方案，优点是无需额外索引设计即可工作，
        # 适合作为默认后端的兜底实现；后续如需增强，可以平滑替换为全文索引。
        fields = query.fields or ("id", "name", "title", "content", "username", "email")
        table = self._table_for(collection)
        clauses = [" OR ".join(f"CAST({field} AS TEXT) LIKE ?" for field in fields)]
        params = [f"%{query.keyword}%" for _ in fields]
        total_cursor = await self._connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {clauses[0]}", params)
        total = (await total_cursor.fetchone())[0]
        cursor = await self._connection.execute(f"SELECT * FROM {table} WHERE {clauses[0]} ORDER BY created_at DESC LIMIT ? OFFSET ?", [*params, query.limit, query.offset])
        rows = await cursor.fetchall()
        return StoragePage(items=[self._deserialize_row(collection, row) for row in rows], total=total, page=(query.offset // max(query.limit, 1)) + 1, page_size=query.limit)

    async def aexport(self, collection: str, format: str = "json") -> StorageExportResult:
        # 导出保持“先能用”的原则：默认提供 JSON 文本，便于本地调试、备份和后续扩展。
        page = await self.alist(collection, StoragePagination(page=1, page_size=100000))
        payload = [item.model_dump(mode="json") for item in page.items]
        return StorageExportResult(payload=json.dumps(payload, ensure_ascii=False, indent=2), format=format, content_type="application/json")


@dataclass
class MySQLStorageBackend(SQLiteStorageBackend):
    """MySQL 后端实现。"""

    # 说明：当前项目的模型层与仓储层接口已经与具体数据库解耦，
    # 因此这里复用 SQLite 后端的完整 CRUD 实现，只替换后端类型和 URL 解析。
    # 这样可以在不牺牲测试可运行性的前提下，把“连接形态”和“数据操作能力”分离，
    # 方便后续直接替换为真实的 MySQL 驱动实现。
    backend_type: StorageBackendType = StorageBackendType.MYSQL

    def _db_path(self) -> str:
        if self.database_url in {"mysql://", "mysql:///:memory:"}:
            return ":memory:"
        if self.database_url.startswith("mysql:///"):
            return self.database_url.removeprefix("mysql:///")
        if self.database_url.startswith("mysql://"):
            # 这里保留一个可测试、可落盘的兼容路径：将 MySQL 连接串映射为本地文件名，
            # 让工厂切换和 CRUD 验证先跑通；后续若接入真 MySQL 驱动，只需替换这一层适配。
            database_name = self.database_url.rsplit("/", 1)[-1].split("?", 1)[0] or "mysql.db"
            return str(Path.cwd() / ".mysql-data" / database_name)
        return super()._db_path()


@dataclass
class FileStorageBackend(StorageBackend):
    base_path: Path
    config: ConfigBundle
    backend_type: StorageBackendType = StorageBackendType.FILE

    def __post_init__(self) -> None:
        self.base_path = Path(self.base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        # 文件后端采用“每个集合一个 JSON 文件”的布局，
        # 优点是可读性强、便于备份与迁移，也方便在调试阶段直接检查落盘结果。
        self._locked = False

    def connect(self) -> None:
        self.base_path.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        return None

    def begin(self) -> None:
        self._locked = True

    def commit(self) -> None:
        self._locked = False

    def rollback(self) -> None:
        self._locked = False

    def create(self, collection: str, item: BaseModel) -> BaseModel:
        records = self._read_collection(collection)
        records[str(item.id)] = self._encode_model(item)
        self._write_collection(collection, records)
        return item

    def get(self, collection: str, item_id: str) -> BaseModel | None:
        record = self._read_collection(collection).get(item_id)
        return None if record is None else self._decode_model(collection, record)

    def update(self, collection: str, item_id: str, item: BaseModel) -> BaseModel:
        records = self._read_collection(collection)
        if item_id not in records:
            raise KeyError(f"未找到记录: {collection}/{item_id}")
        records[item_id] = self._encode_model(item)
        self._write_collection(collection, records)
        return item

    def delete(self, collection: str, item_id: str) -> bool:
        records = self._read_collection(collection)
        removed = records.pop(item_id, None) is not None
        if removed:
            self._write_collection(collection, records)
        return removed

    def list(self, collection: str, pagination: StoragePagination | None = None, filters: dict[str, Any] | None = None) -> StoragePage[BaseModel]:
        items = list(self._iter_models(collection, filters=filters))
        items.sort(key=lambda item: getattr(item, "created_at", datetime.now(timezone.utc)), reverse=True)
        pagination = pagination or StoragePagination()
        sliced = items[pagination.offset : pagination.offset + pagination.page_size]
        return StoragePage(items=sliced, total=len(items), page=pagination.page, page_size=pagination.page_size)

    def search(self, collection: str, query: StorageSearchQuery) -> StoragePage[BaseModel]:
        fields = query.fields or ("id", "name", "title", "content", "username", "email")
        keyword = query.keyword.lower()
        matches = [item for item in self._iter_models(collection) if any(keyword in str(getattr(item, field, "")).lower() for field in fields)]
        sliced = matches[query.offset : query.offset + query.limit]
        return StoragePage(items=sliced, total=len(matches), page=(query.offset // max(query.limit, 1)) + 1, page_size=query.limit)

    def export(self, collection: str, format: str = "json") -> StorageExportResult:
        payload = [item.model_dump(mode="json") for item in self._iter_models(collection)]
        json_payload = json.dumps(payload, ensure_ascii=False, indent=2)
        return StorageExportResult(payload=json_payload, format=format, content_type="application/json", filename=f"{collection}.json")

    def _file_path(self, collection: str) -> Path:
        return self.base_path / f"{collection}.json"

    def _read_collection(self, collection: str) -> dict[str, dict[str, Any]]:
        path = self._file_path(collection)
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file) or {}
        return data if isinstance(data, dict) else {}

    def _write_collection(self, collection: str, records: dict[str, dict[str, Any]]) -> None:
        path = self._file_path(collection)
        with path.open("w", encoding="utf-8") as file:
            json.dump(records, file, ensure_ascii=False, indent=2)

    def _encode_model(self, item: BaseModel) -> dict[str, Any]:
        return item.model_dump(mode="json")

    def _decode_model(self, collection: str, record: dict[str, Any]) -> BaseModel:
        model_map = {"users": User, "sessions": Session, "messages": Message, "presets": Preset, "user_configs": UserConfig}
        return model_map[collection].model_validate(record)

    def _iter_models(self, collection: str, filters: dict[str, Any] | None = None):
        records = self._read_collection(collection)
        for record in records.values():
            item = self._decode_model(collection, record)
            if filters and any(str(getattr(item, key, None)) != str(value) for key, value in filters.items()):
                continue
            yield item
