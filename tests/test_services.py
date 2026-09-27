"""服务层测试：配置、最近使用、任务历史、日志与路径。"""

from __future__ import annotations

import logging
from pathlib import Path

from app.services.history import HistoryRecord, HistoryService
from app.services.logger import setup_logging
from app.services.paths import (
    config_dir,
    history_file,
    log_file,
    logs_dir,
    recent_file,
    settings_file,
    temp_root,
)
from app.services.recent_files import RecentFilesService
from app.services.settings import THEME_DARK, THEME_SYSTEM, SettingsService


def test_paths_use_isolated_environment() -> None:
    assert "packpilot-env" in str(config_dir()) or config_dir().is_dir()
    assert logs_dir().name == "logs"
    assert settings_file().name == "settings.json"
    assert recent_file().name == "recent.json"
    assert history_file().name == "history.json"
    assert log_file().parent == logs_dir()
    assert temp_root().name in {"temp", "PackPilot"}


def test_settings_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    service = SettingsService(path)
    assert service.settings.theme == THEME_SYSTEM
    service.update(theme=THEME_DARK, recent_limit=25, default_format="7z", default_level="ultra")
    assert path.exists()

    reloaded = SettingsService(path)
    assert reloaded.settings.theme == THEME_DARK
    assert reloaded.recent_limit == 25
    assert reloaded.default_format.value == "7z"
    assert reloaded.default_level.value == "ultra"


def test_settings_invalid_values_fallback(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"theme": "unknown", "default_format": "rar", "unknown_field": 1}', encoding="utf-8")
    service = SettingsService(path)
    assert service.settings.theme == THEME_SYSTEM
    assert service.default_format.value == "zip"


def test_settings_corrupted_file_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ this is not json", encoding="utf-8")
    service = SettingsService(path)
    assert service.settings.theme == THEME_SYSTEM
    service.save()
    assert "theme" in path.read_text(encoding="utf-8")


def test_settings_reset(tmp_path: Path) -> None:
    service = SettingsService(tmp_path / "settings.json")
    service.update(theme=THEME_DARK)
    service.reset()
    assert service.settings.theme == THEME_SYSTEM


def test_recent_files_limit_and_dedup(tmp_path: Path) -> None:
    path = tmp_path / "recent.json"
    service = RecentFilesService(path, limit=3)
    files = []
    for index in range(5):
        item = tmp_path / f"文件{index}.zip"
        item.write_bytes(b"x")
        files.append(item)
        service.add(item)
    items = service.items()
    assert len(items) == 3
    assert items[0].path == files[-1]

    service.add(files[-1])
    assert len(service.items()) == 3

    service.remove(files[-1])
    assert all(entry.path != files[-1] for entry in service.items())
    service.clear()
    assert service.items() == []


def test_recent_files_marks_missing(tmp_path: Path) -> None:
    service = RecentFilesService(tmp_path / "recent.json", limit=5)
    missing = tmp_path / "不存在.zip"
    service.add(missing)
    entry = service.items()[0]
    assert not entry.exists
    assert service.existing() == []


def test_recent_files_reload(tmp_path: Path) -> None:
    path = tmp_path / "recent.json"
    item = tmp_path / "a.zip"
    item.write_bytes(b"x")
    RecentFilesService(path, limit=5).add(item)
    reloaded = RecentFilesService(path, limit=5)
    assert len(reloaded.items()) == 1
    assert reloaded.items()[0].path == item


def test_history_service(tmp_path: Path) -> None:
    service = HistoryService(tmp_path / "history.json", limit=10)
    for index in range(3):
        service.add(
            HistoryRecord(
                timestamp=f"2026-09-27 10:0{index}:00",
                kind="压缩",
                title=f"任务{index}",
                source="a",
                target="b",
                status="完成",
                duration=1.5,
            )
        )
    records = service.records()
    assert len(records) == 3
    assert records[0].title == "任务2"
    assert service.records(1)[0].title == "任务2"

    exported = service.export(tmp_path / "历史.txt")
    assert exported.exists()
    assert "任务2" in exported.read_text(encoding="utf-8")

    reloaded = HistoryService(tmp_path / "history.json", limit=10)
    assert len(reloaded.records()) == 3
    service.clear()
    assert service.records() == []


def test_history_limit_enforced(tmp_path: Path) -> None:
    service = HistoryService(tmp_path / "history.json", limit=2)
    for index in range(5):
        service.add(HistoryRecord(timestamp="t", kind="测试", title=f"任务{index}", status="完成"))
    assert len(service.records()) == 2


def test_logger_creates_file(tmp_path: Path) -> None:
    log_path = setup_logging(logging.INFO, log_dir=tmp_path)
    logging.getLogger("packpilot.test").warning("日志测试消息")
    assert log_path.exists()
    assert log_path.parent == tmp_path
