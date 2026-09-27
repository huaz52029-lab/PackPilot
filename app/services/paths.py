"""应用数据/日志/临时目录路径（Windows 优先，支持环境变量覆盖以便测试）。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.version import APP_NAME

ENV_CONFIG_DIR = "PACKPILOT_CONFIG_DIR"
ENV_LOG_DIR = "PACKPILOT_LOG_DIR"
ENV_TEMP_DIR = "PACKPILOT_TEMP_DIR"


def roaming_appdata() -> Path:
    """``%APPDATA%``（Windows 漫游配置目录）。"""

    override = os.environ.get(ENV_CONFIG_DIR)
    if override:
        return Path(override)
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def local_appdata() -> Path:
    """``%LOCALAPPDATA%``（本机数据目录）。"""

    override = os.environ.get(ENV_LOG_DIR)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def config_dir() -> Path:
    return roaming_appdata()


def logs_dir() -> Path:
    return local_appdata() / "logs"


def settings_file() -> Path:
    return config_dir() / "settings.json"


def recent_file() -> Path:
    return config_dir() / "recent.json"


def history_file() -> Path:
    return config_dir() / "history.json"


def log_file() -> Path:
    return logs_dir() / "packpilot.log"


def temp_root() -> Path:
    """PackPilot 专用临时目录（用于打开压缩包内文件等）。"""

    override = os.environ.get(ENV_TEMP_DIR)
    base = Path(override) if override else Path(tempfile.gettempdir()) / APP_NAME
    return base


def open_temp_dir() -> Path:
    return temp_root() / "open"


def ensure_app_dirs() -> None:
    for directory in (config_dir(), logs_dir(), temp_root()):
        directory.mkdir(parents=True, exist_ok=True)
