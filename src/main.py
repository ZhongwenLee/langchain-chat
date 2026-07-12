from __future__ import annotations

from .config import get_config
from .chat_engine import ChatClaude, ChatEngine
from .session_manager import SessionManager
from .storage import StorageFactory
from .tui_app import TUIApp
from .user_manager import UserManager


def build_app() -> TUIApp:
    config = get_config()
    storage = StorageFactory().create(config)
    storage.connect()
    user_manager = UserManager(storage)
    session_manager = SessionManager(storage, user_manager)
    model = ChatClaude(model_name="claude", api_key=config.secrets.api_key)
    return TUIApp(chat_engine=ChatEngine(session_manager=session_manager, user_manager=user_manager, model=model), session_manager=session_manager, user_manager=user_manager)


def main() -> None:
    app = build_app()
    app.refresh_state()


if __name__ == "__main__":
    main()
