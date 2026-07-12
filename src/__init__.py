"""项目入口包。"""

from .config_manager import ConfigBundle, ConfigError, ConfigManager
from .core import ChatChunk, ChatClaude, ChatEngine, ChatEngineInspector, ChatResponse, ChatTurn, SessionManager, SessionManagerError, SessionSummary, UserManager, UserManagerError, UserPreferenceChange
from .models import Message, MessageRole, Preset, PresetScope, Session, TimeStampedModel, User, UserConfig, UserRole
from .preset_manager import PresetManager, PresetSummary
from .storage import FileStorageBackend, MySQLStorageBackend, SQLiteStorageBackend, StorageBackend, StorageBackendType, StorageExportResult, StorageFactory, StoragePagination, StoragePage, StorageSearchQuery
from .tui_app import TUIApp
from .ui_protocol import ConversationPreview, MenuAction, UIAdapter, UIEvent, UIKind, UIResult, UIService, UIState

__all__ = [
    "ChatChunk",
    "ChatClaude",
    "ChatEngine",
    "ChatEngineInspector",
    "ChatResponse",
    "ChatTurn",
    "ConfigBundle",
    "ConfigError",
    "ConfigManager",
    "ConversationPreview",
    "FileStorageBackend",
    "Message",
    "MessageRole",
    "MenuAction",
    "MySQLStorageBackend",
    "Preset",
    "PresetManager",
    "PresetScope",
    "PresetSummary",
    "SQLiteStorageBackend",
    "Session",
    "SessionManager",
    "SessionManagerError",
    "SessionSummary",
    "StorageBackend",
    "StorageBackendType",
    "StorageExportResult",
    "StorageFactory",
    "StoragePagination",
    "StoragePage",
    "StorageSearchQuery",
    "TUIApp",
    "TimeStampedModel",
    "UIAdapter",
    "UIEvent",
    "UIKind",
    "UIResult",
    "UIService",
    "UIState",
    "User",
    "UserConfig",
    "UserManager",
    "UserManagerError",
    "UserPreferenceChange",
    "UserRole",
]
