from __future__ import annotations

from pathlib import Path

from src.config_manager import ConfigManager


def test_config_manager_loads_env_specific_files() -> None:
    base_path = Path(__file__).resolve().parents[1]

    dev = ConfigManager(base_path=base_path, env_name="dev").load()
    test_cfg = ConfigManager(base_path=base_path, env_name="test").load()
    prod = ConfigManager(base_path=base_path, env_name="prod").load()

    assert dev.app.environment.name == "dev"
    assert test_cfg.app.environment.name == "test"
    assert prod.app.environment.name == "prod"

    assert dev.secrets.database_url != test_cfg.secrets.database_url
    assert test_cfg.secrets.database_url != prod.secrets.database_url
    assert dev.secrets.database_url != prod.secrets.database_url

    assert dev.app.debug is True
    assert test_cfg.app.app_name == "langchain-chat-test"
    assert prod.app.debug is False
