"""对话框公共工具：分卷选项、格式下拉、磁盘空间展示。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QComboBox

from app.core.archive_info import ArchiveFormat
from app.core.archive_security import DiskSpaceReport
from app.core.options import CompressionLevel
from app.core.utils import human_size

MB = 1024 * 1024
GB = 1024 * MB

#: 分卷大小选项（None 表示不分卷）
VOLUME_OPTIONS: list[tuple[str, int | None]] = [
    ("不分卷", None),
    ("100 MB", 100 * MB),
    ("500 MB", 500 * MB),
    ("1 GB", GB),
    ("2 GB", 2 * GB),
    ("自定义", -1),
]


def fill_format_combo(combo: QComboBox, *, current: ArchiveFormat | None = None) -> None:
    combo.clear()
    for archive_format in ArchiveFormat:
        combo.addItem(archive_format.display_name, archive_format.value)
    if current is not None:
        index = combo.findData(current.value)
        if index >= 0:
            combo.setCurrentIndex(index)


def selected_format(combo: QComboBox) -> ArchiveFormat:
    data = combo.currentData()
    try:
        return ArchiveFormat(str(data))
    except ValueError:
        return ArchiveFormat.ZIP


def fill_level_combo(combo: QComboBox, *, current: CompressionLevel | None = None) -> None:
    combo.clear()
    for level in CompressionLevel:
        combo.addItem(level.label, level.value)
        combo.setItemData(combo.count() - 1, level.description, 3)  # Qt.ToolTipRole
    if current is not None:
        index = combo.findData(current.value)
        if index >= 0:
            combo.setCurrentIndex(index)


def selected_level(combo: QComboBox) -> CompressionLevel:
    data = combo.currentData()
    try:
        return CompressionLevel(str(data))
    except ValueError:
        return CompressionLevel.NORMAL


def selected_volume_size(combo: QComboBox, custom_spin_value: int) -> int | None:
    data = combo.currentData()
    if data is None:
        return None
    if data == -1:
        return custom_spin_value
    return int(data)


def disk_space_text(report: DiskSpaceReport) -> str:
    status = "充足" if report.sufficient else "不足"
    return (
        f"需要空间：{human_size(report.required)}    当前剩余：{human_size(report.available)}"
        f"    → 空间{status}"
    )


def default_output_directory(settings_dir: Path | None, fallback: Path | None = None) -> Path:
    if settings_dir is not None and settings_dir.exists():
        return settings_dir
    if fallback is not None:
        return fallback
    return Path.home() / "Documents"
