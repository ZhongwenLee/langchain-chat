from __future__ import annotations

from pathlib import Path

import pytest

from src.config_manager import ConfigError, ConfigManager


@pytest.fixture()
def config_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """构造一个独立的测试配置目录，避免污染真实环境。"""

    (tmp_path / "config").mkdir()
    (tmp_path / ".env").write_text(
        "API_KEY=test-key\nDATABASE_URL=sqlite:///test.db\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "config.yaml").write_text(
        "app_name: test-app\ndebug: true\nenvironment:\n  name: test\n  overrides:\n    feature_flag: true\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "logging.yaml").write_text(
        "version: 1\nroot:\n  level: INFO\n  handlers: [console]\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return tmp_path


def test_config_manager_loads_all_configs(config_root: Path) -> None:
    """验证配置中心可以一次性加载全部配置。"""

    # 这个用例的核心价值是确认“文件读取 + 环境合并 + 类型转换”这条链路没有断点。

    config = ConfigManager(base_path=config_root).load()

    assert config.secrets.api_key == "test-key"
    assert config.secrets.database_url == "sqlite:///test.db"
    assert config.app.app_name == "test-app"
    assert config.app.debug is True
    assert config.app.environment.name == "test"
    assert config.app.environment.overrides == {"feature_flag": True}
    assert config.logging.version == 1


def test_config_manager_raises_for_missing_secret(tmp_path: Path) -> None:
    """验证缺失敏感配置时会抛出清晰错误。"""

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("app_name: test-app\n", encoding="utf-8")
    (tmp_path / "config" / "logging.yaml").write_text("version: 1\n", encoding="utf-8")
    (tmp_path / ".env").write_text("API_KEY=\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="缺失必填配置: API_KEY"):
        ConfigManager(base_path=tmp_path).load()


def test_config_manager_supports_environment_override(config_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证环境变量可以覆盖 .env 中的同名配置。"""

    monkeypatch.setenv("API_KEY", "override-key")
    config = ConfigManager(base_path=config_root).load()

    assert config.secrets.api_key == "override-key"
