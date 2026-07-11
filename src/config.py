"""配置中心统一入口。"""

from __future__ import annotations

from pathlib import Path

from .config_manager import ConfigBundle, ConfigManager


_default_manager: ConfigManager | None = None
_default_config: ConfigBundle | None = None


def get_config(base_path: str | Path | None = None) -> ConfigBundle:
    """获取全局配置对象。

    如果传入 base_path，则会基于指定路径重新加载；
    否则复用当前进程中的默认单例，便于后续模块统一调用。
    """

    global _default_manager, _default_config

    if base_path is not None:
        return ConfigManager(base_path=base_path).load()

    if _default_manager is None:
        _default_manager = ConfigManager()
    if _default_config is None:
        _default_config = _default_manager.load()
    return _default_config
