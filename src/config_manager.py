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
    """环境覆盖配置。

    这个对象会显式记录当前运行环境，以及从环境专属配置文件中
    解析出来的覆盖项，便于上层代码和测试快速判断“当前到底跑在谁的配置下”。
    """

    name: str = "dev"
    overrides: dict[str, Any] = field(default_factory=dict)
    env_file: str | None = None
    config_file: str | None = None
    source: str = "default"
    merged_keys: tuple[str, ...] = ()


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

    负责从 APP_ENV 指定的环境中加载 .env.{env} 与 config.{env}.yaml，
    并以基础配置作为默认值、环境专属配置作为覆盖值。
    """

    def __init__(self, base_path: str | Path | None = None, env_name: str | None = None) -> None:
        self.base_path = Path(base_path or Path.cwd())
        self._config_dir = self.base_path / "config"
        self._env_name = self._normalize_env_name(env_name or os.getenv("APP_ENV", "dev"))

    def load(self) -> ConfigBundle:
        """加载并校验全部配置。"""

        base_env = self._load_env_file(self.base_path / ".env")
        env_env = self._load_env_file(self.base_path / f".env.{self._env_name}")
        env_values = self._merge_dicts(base_env, env_env)
        config_values = self._load_merged_yaml(
            self._config_dir / "config.yaml",
            self._config_dir / f"config.{self._env_name}.yaml",
        )
        logging_values = self._load_merged_yaml(
            self._config_dir / "logging.yaml",
            self._config_dir / f"logging.{self._env_name}.yaml",
        )
        presets_values = self._load_merged_yaml(
            self._config_dir / "presets.yaml",
            self._config_dir / f"presets.{self._env_name}.yaml",
            optional=True,
        )

        secrets = SecretConfig(
            api_key=self._require_value("API_KEY", env_values),
            database_url=self._require_value("DATABASE_URL", env_values),
        )
        app_config = self._build_app_config(config_values)
        logging_config = self._build_logging_config(logging_values)
        environment = self._build_environment_config(config_values, env_values)
        app_config = AppConfig(app_name=app_config.app_name, debug=app_config.debug, environment=environment)

        return ConfigBundle(
            app=app_config,
            logging=logging_config,
            secrets=secrets,
            raw_environment=env_values,
            presets=presets_values,
        )

    def _normalize_env_name(self, env_name: str) -> str:
        """规范化环境名，避免空白值或大小写差异造成配置路径歧义。"""

        normalized = env_name.strip().lower()
        if not normalized:
            raise ConfigError("APP_ENV 不能为空")
        return normalized

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

    def _load_merged_yaml(self, base_path: Path, env_path: Path, optional: bool = False) -> dict[str, Any]:
        """加载基础 YAML，并允许环境专属 YAML 对其做深度合并覆盖。"""

        if not base_path.exists():
            if optional and not env_path.exists():
                return {}
            if optional:
                return self._load_yaml_file(env_path)
            raise ConfigError(f"配置文件不存在: {base_path}")
        base_values = self._load_yaml_file(base_path)
        env_values = self._load_yaml_file(env_path) if env_path.exists() else {}
        return self._merge_dicts(base_values, env_values)

    def _merge_dicts(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """递归合并两个字典。

        这里只对字典做深度合并，列表采用整体覆盖，以减少配置含义歧义。
        这能让 config.dev.yaml 只写差异项，而不会把整份基础配置复制一遍。
        """

        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_dicts(merged[key], value)
            else:
                merged[key] = value
        return merged

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
        return AppConfig(app_name=app_name, debug=debug)

    def _build_environment_config(self, data: dict[str, Any], env_values: dict[str, str]) -> EnvironmentConfig:
        """构造环境信息，方便测试和运行时校验环境隔离是否生效。"""

        env_data = data.get("environment", {})
        if not isinstance(env_data, dict):
            raise ConfigError("config.yaml 中 environment 必须是字典")
        overrides = env_data.get("overrides", {})
        if not isinstance(overrides, dict):
            raise ConfigError("config.yaml 中 environment.overrides 必须是字典")
        return EnvironmentConfig(
            name=self._env_name,
            overrides=dict(overrides),
            env_file=f".env.{self._env_name}" if (self.base_path / f".env.{self._env_name}").exists() else ".env",
            config_file=f"config.{self._env_name}.yaml" if (self._config_dir / f"config.{self._env_name}.yaml").exists() else "config.yaml",
            source=f"{self._env_name}",
            merged_keys=tuple(sorted(k for k in env_values.keys() if k in {"APP_ENV", "API_KEY", "DATABASE_URL"})),
        )

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
