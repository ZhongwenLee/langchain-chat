from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from src.config_manager import ConfigManager
from src.preset_manager import PresetManager


@pytest.fixture()
def preset_config_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / ".env").write_text(
        "API_KEY=test-key\nDATABASE_URL=sqlite:///test.db\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "config.yaml").write_text(
        "app_name: test-app\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "logging.yaml").write_text(
        "version: 1\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "presets.yaml").write_text(
        """system_presets:
  - id: default-assistant
    name: 默认助手
    scope: global
    model_name: gpt-4.1
    temperature: 0.7
    prompt_template: |
      你是一个专业助手。
  - id: coding-assistant
    name: 编程助手
    scope: global
    model_name: gpt-4.1
    temperature: 0.2
    prompt_template: |
      你是一个资深工程师。
user_preset_schema:
  enabled: true
  storage_hint: 本地用户预设占位
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return tmp_path


def test_preset_manager_lists_builtin_presets(preset_config_root: Path) -> None:
    config = ConfigManager(base_path=preset_config_root).load()
    manager = PresetManager(config, base_path=preset_config_root)

    presets = manager.list_builtin_presets()

    assert [preset.name for preset in presets] == ["默认助手", "编程助手"]
    assert all(preset.source == "builtin" for preset in presets)


def test_preset_manager_builds_system_prompt(preset_config_root: Path) -> None:
    config = ConfigManager(base_path=preset_config_root).load()
    manager = PresetManager(config, base_path=preset_config_root)

    prompt = manager.build_system_prompt("coding-assistant")

    assert "编程助手" in prompt
    assert "资深工程师" in prompt


def test_preset_manager_exposes_session_context(preset_config_root: Path) -> None:
    config = ConfigManager(base_path=preset_config_root).load()
    manager = PresetManager(config, base_path=preset_config_root)

    context = manager.create_session_prompt_context()

    assert context["preset_name"] == "默认助手"
    assert context["system_prompt"].startswith("角色预设：默认助手")
    UUID(context["preset_id"])
