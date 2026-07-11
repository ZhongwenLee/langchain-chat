from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from .config_manager import ConfigBundle, ConfigError
from .models import Preset, PresetScope


@dataclass(frozen=True)
class PresetSummary:
    """用于列表展示的预设摘要。"""

    id: str
    name: str
    description: str
    scope: PresetScope
    model_name: str
    temperature: float
    source: str


class PresetManager:
    """预设 Prompt 管理器。

    这个类负责统一处理“内置预设 + 用户自定义预设”的检索逻辑，
    并为会话创建阶段提供最终 system prompt 入口。
    """

    def __init__(self, config: ConfigBundle, base_path: str | Path | None = None) -> None:
        self._config = config
        self._base_path = Path(base_path or Path.cwd())
        self._preset_aliases: dict[str, UUID] = {}
        self._builtin_presets = self._load_builtin_presets()
        self._custom_preset_schema = self._load_custom_preset_schema()

    def list_builtin_presets(self) -> list[PresetSummary]:
        """返回系统内置预设列表。"""

        return [self._to_summary(preset, source="builtin") for preset in self._builtin_presets]

    def list_all_presets(self) -> list[PresetSummary]:
        """返回全部可见预设摘要，当前先包含内置预设和预留的用户预设入口。"""

        presets = self.list_builtin_presets()
        if self._custom_preset_schema.get("enabled"):
            presets.append(
                PresetSummary(
                    id="user-custom-placeholder",
                    name="自定义预设",
                    description=str(self._custom_preset_schema.get("storage_hint", "用户自定义预设")),
                    scope=PresetScope.PRIVATE,
                    model_name="",
                    temperature=0.0,
                    source="user-schema",
                )
            )
        return presets

    def get_preset(self, preset_id: str | UUID) -> Preset:
        """按 ID、名称或 YAML 中声明的文本别名获取预设。"""

        if isinstance(preset_id, UUID):
            for preset in self._builtin_presets:
                if preset.id == preset_id:
                    return preset

        preset_key = self._slugify(str(preset_id))
        alias_id = self._preset_aliases.get(preset_key)
        if alias_id is not None:
            for preset in self._builtin_presets:
                if preset.id == alias_id:
                    return preset

        raise ConfigError(f"未找到预设: {preset_id}")

    def select_preset(self, preset_id: str | UUID | None = None) -> Preset:
        """选择预设；未传入时回退到第一个系统内置预设。"""

        if preset_id is None:
            return self._builtin_presets[0]
        return self.get_preset(preset_id)

    def build_system_prompt(self, preset_id: str | UUID | None = None) -> str:
        """生成最终 system prompt，供新会话创建时直接使用。"""

        preset = self.select_preset(preset_id)
        header = f"角色预设：{preset.name}\n模型建议：{preset.model_name}\n温度：{preset.temperature}"
        return f"{header}\n\n{preset.prompt_template}".strip()

    def create_session_prompt_context(self, preset_id: str | UUID | None = None) -> dict[str, Any]:
        """给会话创建流程预留上下文结构，方便后续和 Session/Message 编排对接。"""

        preset = self.select_preset(preset_id)
        return {
            "preset_id": str(preset.id),
            "preset_name": preset.name,
            "model_name": preset.model_name,
            "temperature": preset.temperature,
            "system_prompt": self.build_system_prompt(preset.id),
        }

    def _load_builtin_presets(self) -> list[Preset]:
        presets_data = self._config.presets.get("system_presets", [])
        if not isinstance(presets_data, list) or not presets_data:
            raise ConfigError("presets.yaml 中缺少 system_presets")

        presets: list[Preset] = []
        for index, item in enumerate(presets_data):
            if not isinstance(item, dict):
                raise ConfigError(f"presets.yaml 中第 {index + 1} 个预设格式非法")

            preset_id = item.get("id")
            if not isinstance(preset_id, str) or not preset_id.strip():
                raise ConfigError(f"presets.yaml 中第 {index + 1} 个预设缺少 id")

            parsed = Preset.model_validate(
                {
                    "id": self._build_preset_uuid(preset_id),
                    "name": item.get("name", preset_id),
                    "scope": item.get("scope", PresetScope.GLOBAL),
                    "prompt_template": item.get("prompt_template", ""),
                    "model_name": item.get("model_name", ""),
                    "temperature": item.get("temperature", 0.7),
                    "owner_id": None,
                }
            )
            presets.append(parsed)
            self._preset_aliases[self._slugify(preset_id)] = parsed.id
            self._preset_aliases[self._slugify(parsed.name)] = parsed.id
        return presets

    def _build_preset_uuid(self, preset_id: str) -> UUID:
        # YAML 里的内置预设 ID 主要承担“稳定索引”的作用，
        # 不要求用户直接感知 UUID，因此这里用 uuid5 做确定性映射。
        # 这样可以保证同一个文本 ID 在不同环境里映射出同一个 UUID。
        return uuid5(NAMESPACE_URL, f"langchain-chat:preset:{preset_id}")

    def _load_custom_preset_schema(self) -> dict[str, Any]:
        schema = self._config.presets.get("user_preset_schema", {})
        return schema if isinstance(schema, dict) else {}

    def _slugify(self, value: str) -> str:
        # 允许用更自然的短标识来选择预设，比如 coding-assistant 或 编程助手。
        return value.strip().lower().replace(" ", "-")

    def _to_summary(self, preset: Preset, source: str) -> PresetSummary:
        return PresetSummary(
            id=str(preset.id),
            name=preset.name,
            description=preset.prompt_template.splitlines()[0][:80],
            scope=preset.scope,
            model_name=preset.model_name,
            temperature=preset.temperature,
            source=source,
        )
