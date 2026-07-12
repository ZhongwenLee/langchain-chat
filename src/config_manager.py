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
    """环境覆盖配置。"""

    name: str = "dev"
    overrides: dict[str, Any] = field(default_factory=dict)
    env_file: str | None = None
    config_file: str | None = None
    source: str = "default"
    merged_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class AppConfig:
    app_name: str
    debug: bool = False
    environment: EnvironmentConfig = field(default_factory=EnvironmentConfig)


@dataclass(frozen=True)
class LoggingConfig:
    version: int
    disable_existing_loggers: bool = False
    root: dict[str, Any] = field(default_factory=dict)
    loggers: dict[str, Any] = field(default_factory=dict)
    handlers: dict[str, Any] = field(default_factory=dict)
    formatters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SecretConfig:
    api_key: str
    database_url: str


@dataclass(frozen=True)
class ConfigBundle:
    app: AppConfig
    logging: LoggingConfig
    secrets: SecretConfig
    raw_environment: dict[str, str] = field(default_factory=dict)
    presets: dict[str, Any] = field(default_factory=dict)


class ConfigManager:
    def __init__(self, base_path: str | Path | None = None, env_name: str | None = None) -> None:
        self.base_path = Path(base_path or Path.cwd())
        self._config_dir = self.base_path / "config"
        self._env_name = self._normalize_env_name(env_name or os.getenv("APP_ENV", "dev"))

    def load(self) -> ConfigBundle:
        base_env = self._load_env_file(self.base_path / ".env")
        env_env = self._load_env_file(self.base_path / f".env.{self._env_name}")
        env_values = self._merge_dicts(base_env, env_env)
        config_values = self._load_config_yaml("config.yaml", f"config.{self._env_name}.yaml")
        logging_values = self._load_config_yaml("logging.yaml", f"logging.{self._env_name}.yaml", optional=True)
        presets_values = self._load_config_yaml("presets.yaml", f"presets.{self._env_name}.yaml", optional=True)
        secrets = SecretConfig(api_key=self._require_value("API_KEY", env_values), database_url=self._require_value("DATABASE_URL", env_values))
        app_config = self._build_app_config(config_values)
        logging_config = self._build_logging_config(logging_values)
        environment = self._build_environment_config(config_values, env_values)
        app_config = AppConfig(app_name=app_config.app_name, debug=app_config.debug, environment=environment)
        return ConfigBundle(app=app_config, logging=logging_config, secrets=secrets, raw_environment=env_values, presets=presets_values)

    def _normalize_env_name(self, env_name: str) -> str:
        normalized = env_name.strip().lower()
        if not normalized:
            raise ConfigError("APP_ENV 不能为空")
        return normalized

    def _load_env_file(self, path: Path) -> dict[str, str]:
        file_values = dotenv_values(path) if path.exists() else {}
        merged = {**file_values, **os.environ}
        return {key: str(value) for key, value in merged.items() if value is not None}

    def _load_yaml_file(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise ConfigError(f"配置文件不存在: {path}")
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        if not isinstance(data, dict):
            raise ConfigError(f"配置文件格式错误，期望字典结构: {path}")
        return data

    def _load_config_yaml(self, base_relative: str, env_relative: str, optional: bool = False) -> dict[str, Any]:
        candidates = [self.base_path / base_relative, self.base_path / "config" / base_relative]
        env_candidates = [self.base_path / env_relative, self.base_path / "config" / env_relative]
        base_path = next((path for path in candidates if path.exists()), None)
        env_path = next((path for path in env_candidates if path.exists()), None)
        if base_path is None:
            if optional and env_path is not None:
                return self._load_yaml_file(env_path)
            if optional:
                return {}
            raise ConfigError(f"配置文件不存在: {candidates[0]}")
        base_values = self._load_yaml_file(base_path)
        env_values = self._load_yaml_file(env_path) if env_path is not None else {}
        return self._merge_dicts(base_values, env_values)

    def _merge_dicts(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_dicts(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _require_value(self, key: str, values: dict[str, str]) -> str:
        value = values.get(key)
        if value is None or not str(value).strip():
            raise ConfigError(f"缺失必填配置: {key}")
        return str(value)

    def _build_app_config(self, data: dict[str, Any]) -> AppConfig:
        app_name = data.get("app_name")
        if not isinstance(app_name, str) or not app_name.strip():
            raise ConfigError("config.yaml 中缺失或非法的 app_name")
        return AppConfig(app_name=app_name, debug=bool(data.get("debug", False)))

    def _build_environment_config(self, data: dict[str, Any], env_values: dict[str, str]) -> EnvironmentConfig:
        env_data = data.get("environment", {})
        if not isinstance(env_data, dict):
            raise ConfigError("config.yaml 中 environment 必须是字典")
        overrides = env_data.get("overrides", {})
        if not isinstance(overrides, dict):
            raise ConfigError("config.yaml 中 environment.overrides 必须是字典")
        config_file = f"config.{self._env_name}.yaml" if (
            (self.base_path / f"config.{self._env_name}.yaml").exists()
            or (self.base_path / "config" / f"config.{self._env_name}.yaml").exists()
        ) else "config.yaml"
        return EnvironmentConfig(
            name=self._env_name,
            overrides=dict(overrides),
            env_file=f".env.{self._env_name}" if (self.base_path / f".env.{self._env_name}").exists() else ".env",
            config_file=config_file,
            source=self._env_name,
            merged_keys=tuple(sorted(k for k in env_values.keys() if k in {"APP_ENV", "API_KEY", "DATABASE_URL", "MODEL_NAME"})),
        )

    def _build_logging_config(self, data: dict[str, Any]) -> LoggingConfig:
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
