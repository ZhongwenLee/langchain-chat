from __future__ import annotations

from pathlib import Path

import pytest

from src.config_manager import ConfigError, ConfigManager


@pytest.fixture()
def config_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """构造一个独立的测试配置目录，避免污染真实环境。"""

    (tmp_path / "config").mkdir()
    (tmp_path / ".env").write_text(
        "APP_ENV=dev\nAPI_KEY=base-key\nDATABASE_URL=sqlite:///base.db\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.dev").write_text(
        "API_KEY=dev-key\nDATABASE_URL=sqlite:///dev.db\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.test").write_text(
        "API_KEY=test-key\nDATABASE_URL=sqlite:///:memory:\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "config.yaml").write_text(
        "app_name: test-app\ndebug: false\nenvironment:\n  overrides:\n    feature_flag: false\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "config.dev.yaml").write_text(
        "debug: true\nenvironment:\n  overrides:\n    feature_flag: true\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "config.test.yaml").write_text(
        "app_name: test-app-ci\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "logging.yaml").write_text(
        "version: 1\nroot:\n  level: INFO\n  handlers: [console]\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "logging.dev.yaml").write_text(
        "root:\n  level: DEBUG\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "presets.yaml").write_text(
        "system_presets:\n  - id: default-assistant\n    name: 默认助手\n    scope: global\n    model_name: gpt-4.1\n    temperature: 0.7\n    prompt_template: |\n      你是一个专业助手。\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    return tmp_path


def test_config_manager_loads_environment_specific_configs(config_root: Path) -> None:
    """验证配置中心会自动读取环境专属文件并覆盖基础配置。"""

    config = ConfigManager(base_path=config_root, env_name="dev").load()

    assert config.secrets.api_key == "dev-key"
    assert config.secrets.database_url == "sqlite:///dev.db"
    assert config.app.app_name == "test-app"
    assert config.app.debug is True
    assert config.app.environment.name == "dev"
    assert config.app.environment.overrides == {"feature_flag": True}
    assert config.app.environment.env_file == ".env.dev"
    assert config.app.environment.config_file == "config.dev.yaml"
    assert config.logging.root["level"] == "DEBUG"
    assert config.raw_environment["APP_ENV"] == "dev"


def test_config_manager_supports_test_environment_isolation(config_root: Path) -> None:
    """验证 test 环境会使用独立数据库与独立密钥。"""

    config = ConfigManager(base_path=config_root, env_name="test").load()

    assert config.secrets.api_key == "test-key"
    assert config.secrets.database_url == "sqlite:///:memory:"
    assert config.app.app_name == "test-app-ci"
    assert config.app.environment.name == "test"


def test_config_manager_raises_for_missing_secret(tmp_path: Path) -> None:
    """验证缺失敏感配置时会抛出清晰错误。"""

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("app_name: test-app\n", encoding="utf-8")
    (tmp_path / "config" / "logging.yaml").write_text("version: 1\n", encoding="utf-8")
    (tmp_path / ".env").write_text("APP_ENV=dev\nAPI_KEY=\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="缺失必填配置: API_KEY"):
        ConfigManager(base_path=tmp_path, env_name="dev").load()


def test_config_manager_supports_environment_override(config_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证进程环境变量可以覆盖 .env 中的同名配置。"""

    monkeypatch.setenv("API_KEY", "override-key")
    config = ConfigManager(base_path=config_root, env_name="dev").load()

    assert config.secrets.api_key == "override-key"
