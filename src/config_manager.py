from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values


class ConfigError(RuntimeError):
    """配置加载失败时抛出的统一异常。"""


@dataclass(frozen=True)
class EnvironmentConfig:
    """环境覆盖配置预留结构。

    后续如果需要按开发、测试、生产等环境做差异化配置，
    可以直接在这里扩展新的字段，而不必改动整体加载流程。
    """

    name: str = "development"
    overrides: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AppConfig:
    """应用级全局配置。"""

    app_name: str
    debug: bool = False
    environment: EnvironmentConfig = field(default_factory=EnvironmentConfig)


@dataclass(frozen=True)
class LoggingConfig:
    """日志配置。"""

    version: int
    disable_existing_loggers: bool = False
    root: dict[str, Any] = field(default_factory=dict)
    loggers: dict[str, Any] = field(default_factory=dict)
    handlers: dict[str, Any] = field(default_factory=dict)
    formatters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SecretConfig:
    """敏感信息配置，统一从 .env 读取。"""

    api_key: str
    database_url: str


@dataclass(frozen=True)
class ConfigBundle:
    """配置中心统一返回的配置对象。"""

    app: AppConfig
    logging: LoggingConfig
    secrets: SecretConfig
    raw_environment: dict[str, str] = field(default_factory=dict)
    presets: dict[str, Any] = field(default_factory=dict)


class ConfigManager:
    """统一配置加载器。

    负责从 .env、config.yaml、config/logging.yaml、config/presets.yaml 加载配置，
    并在缺失关键配置时给出明确错误信息。
    """

    def __init__(self, base_path: str | Path | None = None) -> None:
        self.base_path = Path(base_path or Path.cwd())
        self._config_dir = self.base_path / "config"

    def load(self) -> ConfigBundle:
        """加载并校验全部配置。"""

        env_values = self._load_env_file(self.base_path / ".env")
        app_values = self._load_yaml_file(self._config_dir / "config.yaml")
        logging_values = self._load_yaml_file(self._config_dir / "logging.yaml")
        presets_path = self._config_dir / "presets.yaml"
        presets_values = self._load_yaml_file(presets_path) if presets_path.exists() else {}

        secrets = SecretConfig(
            api_key=self._require_value("API_KEY", env_values),
            database_url=self._require_value("DATABASE_URL", env_values),
        )
        app_config = self._build_app_config(app_values)
        logging_config = self._build_logging_config(logging_values)

        return ConfigBundle(
            app=app_config,
            logging=logging_config,
            secrets=secrets,
            raw_environment=env_values,
            presets=presets_values,
        )

    def _load_env_file(self, path: Path) -> dict[str, str]:
        """加载 .env 文件，并与当前进程环境变量合并。"""

        # 合并顺序刻意让进程环境变量覆盖 .env。
        # 这样更符合本地开发、CI 和部署环境的常见预期：外部注入的配置优先级更高。
        file_values = dotenv_values(path) if path.exists() else {}
        merged = {**file_values, **os.environ}
        return {key: str(value) for key, value in merged.items() if value is not None}

    def _load_yaml_file(self, path: Path) -> dict[str, Any]:
        """加载 YAML 文件，缺失时抛出明确异常。"""

        if not path.exists():
            raise ConfigError(f"配置文件不存在: {path}")
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        if not isinstance(data, dict):
            raise ConfigError(f"配置文件格式错误，期望字典结构: {path}")
        return data

    def _require_value(self, key: str, values: dict[str, str]) -> str:
        """读取必填项，缺失时给出清晰报错。"""

        value = values.get(key)
        if not value:
            raise ConfigError(f"缺失必填配置: {key}")
        return value

    def _build_app_config(self, data: dict[str, Any]) -> AppConfig:
        """将原始字典转换为强类型应用配置。"""

        # 这里先做最小必要校验，再构造领域对象。
        # 这样可以把“配置格式错误”尽早暴露，而不是把脏数据带到后续业务流程里。
        app_name = data.get("app_name")
        if not isinstance(app_name, str) or not app_name.strip():
            raise ConfigError("config.yaml 中缺失或非法的 app_name")

        debug = bool(data.get("debug", False))
        env_data = data.get("environment", {})
        if not isinstance(env_data, dict):
            raise ConfigError("config.yaml 中 environment 必须是字典")

        environment = EnvironmentConfig(
            name=str(env_data.get("name", "development")),
            overrides=dict(env_data.get("overrides", {})) if isinstance(env_data.get("overrides", {}), dict) else {},
        )
        return AppConfig(app_name=app_name, debug=debug, environment=environment)

    def _build_logging_config(self, data: dict[str, Any]) -> LoggingConfig:
        """将日志配置转换为强类型结构。"""

        version = data.get("version")
        if not isinstance(version, int):
            raise ConfigError("config/logging.yaml 中缺失或非法的 version")
        return LoggingConfig(
            version=version,
            disable_existing_loggers=bool(data.get("disable_existing_loggers", False)),
            root=dict(data.get("root", {})) if isinstance(data.get("root", {}), dict) else {},
            loggers=dict(data.get("loggers", {})) if isinstance(data.get("loggers", {}), dict) else {},
            handlers=dict(data.get("handlers", {})) if isinstance(data.get("handlers", {}), dict) else {},
            formatters=dict(data.get("formatters", {})) if isinstance(data.get("formatters", {}), dict) else {},
        )
