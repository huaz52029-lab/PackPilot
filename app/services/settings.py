"""应用配置（JSON，保存到 ``%APPDATA%\\PackPilot\\settings.json``）。"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.core.archive_info import ArchiveFormat
from app.core.options import CompressionLevel
from app.core.utils import atomic_write_json, read_json
from app.services import paths

logger = logging.getLogger(__name__)

THEME_SYSTEM = "system"
THEME_LIGHT = "light"
THEME_DARK = "dark"
THEMES = {THEME_SYSTEM: "跟随系统", THEME_LIGHT: "浅色", THEME_DARK: "深色"}


@dataclass
class Settings:
    """可持久化的用户配置。"""

    theme: str = THEME_SYSTEM
    window_geometry: str = ""
    window_state: str = ""
    task_panel_visible: bool = True
    recent_limit: int = 10
    history_limit: int = 500
    last_directory: str = ""
    last_extract_directory: str = ""
    default_format: str = ArchiveFormat.ZIP.value
    default_level: str = CompressionLevel.NORMAL.value
    default_volume: str = "none"
    hash_algorithm: str = "sha256"
    confirm_delete: bool = True
    confirm_overwrite: bool = True
    safety_enabled: bool = True
    smart_extract: bool = True
    open_file_after_extract: bool = False
    show_hidden_details: bool = True
    file_association_installed: bool = False
    context_menu_installed: bool = False
    check_updates_on_start: bool = False
    recent_sort_state: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> Settings:
        known = set(cls.__dataclass_fields__)
        data = {key: value for key, value in payload.items() if key in known}
        try:
            settings = cls(**data)  # type: ignore[arg-type]
        except TypeError:
            logger.warning("配置文件存在无法识别的字段，已使用默认配置")
            settings = cls()
        if settings.theme not in THEMES:
            settings.theme = THEME_SYSTEM
        return settings


class SettingsService:
    """读写配置，写入过程使用原子替换避免损坏。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else paths.settings_file()
        self.settings = self.load()

    def load(self) -> Settings:
        payload = read_json(self.path, None)
        if isinstance(payload, dict):
            return Settings.from_dict(payload)
        return Settings()

    def save(self) -> None:
        try:
            atomic_write_json(self.path, self.settings.to_dict())
        except OSError as exc:  # pragma: no cover - 磁盘异常
            logger.error("保存配置失败：%s", exc)

    def update(self, **changes: object) -> Settings:
        for key, value in changes.items():
            if not hasattr(self.settings, key):
                continue
            setattr(self.settings, key, value)
        self.save()
        return self.settings

    def reset(self) -> Settings:
        self.settings = Settings()
        self.save()
        return self.settings

    # 便捷属性 --------------------------------------------------------
    @property
    def theme(self) -> str:
        return self.settings.theme

    @property
    def recent_limit(self) -> int:
        return max(1, min(50, self.settings.recent_limit))

    @property
    def default_format(self) -> ArchiveFormat:
        try:
            return ArchiveFormat(self.settings.default_format)
        except ValueError:
            return ArchiveFormat.ZIP

    @property
    def default_level(self) -> CompressionLevel:
        try:
            return CompressionLevel(self.settings.default_level)
        except ValueError:
            return CompressionLevel.NORMAL

    @property
    def last_directory(self) -> Path | None:
        text = self.settings.last_directory
        return Path(text) if text else None

    @property
    def last_extract_directory(self) -> Path | None:
        text = self.settings.last_extract_directory
        return Path(text) if text else None

    def remember_directory(self, directory: Path) -> None:
        self.update(last_directory=str(directory))

    def remember_extract_directory(self, directory: Path) -> None:
        self.update(last_extract_directory=str(directory))
