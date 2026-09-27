"""GUI 冒烟测试：主窗口构建、打开压缩包、浏览条目、主题切换、任务面板与拖放。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.core.options import CreateOptions
from app.gui.main_window import MainWindow
from app.gui.theme import apply_theme, resolve_theme
from app.services.history import HistoryService
from app.services.recent_files import RecentFilesService
from app.services.settings import SettingsService
from app.tasks.base_task import TaskStatus


@pytest.fixture
def window(qtbot, tmp_path: Path) -> MainWindow:  # type: ignore[no-untyped-def]
    settings = SettingsService(tmp_path / "settings.json")
    recent = RecentFilesService(tmp_path / "recent.json", limit=5)
    history = HistoryService(tmp_path / "history.json")
    main_window = MainWindow(settings=settings, recent=recent, history=history)
    qtbot.addWidget(main_window)
    return main_window


@pytest.fixture
def archive(tmp_path: Path, sample_tree: Path) -> Path:
    from app.core.archive_manager import ArchiveManager

    target = tmp_path / "图形界面.zip"
    ArchiveManager().create_archive(CreateOptions(target=target, sources=[sample_tree]))
    return target


def test_window_construction(window: MainWindow) -> None:
    assert "PackPilot" in window.windowTitle()
    assert window.stack.currentWidget() is window.home_page
    assert window.main_toolbar is not None
    assert window.task_panel is not None
    assert window.menuBar().actions(), "菜单栏应包含菜单"


def test_open_archive_populates_tree(window: MainWindow, archive: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
    window.open_archive(archive)
    qtbot.wait(50)
    assert window.current_archive == archive
    assert window.stack.currentWidget() is window.archive_view
    assert window.archive_view.tree.topLevelItemCount() == 1
    assert "文件：3" in window.archive_view.summary_label.text()
    assert window.recent.items()[0].path == archive
    assert archive.name in window.windowTitle()


def test_select_and_collect_paths(window: MainWindow, archive: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
    window.open_archive(archive)
    qtbot.wait(20)
    tree = window.archive_view.tree
    top = tree.topLevelItem(0)
    top.setExpanded(True)
    assert top.childCount() >= 1
    child = top.child(0)
    child.setSelected(True)
    qtbot.wait(10)
    paths = window.archive_view.selected_paths()
    assert paths and paths[0].startswith("测试项目")


def test_copy_entry_paths_uses_clipboard(window: MainWindow, archive: Path) -> None:
    from PySide6.QtWidgets import QApplication

    window.open_archive(archive)
    window.copy_entry_paths(["a.txt", "b.txt"])
    assert QApplication.clipboard().text() == "a.txt\nb.txt"


def test_entry_properties_dialog_text(window: MainWindow, archive: Path) -> None:
    window.open_archive(archive)
    info = window.manager.list_archive(archive)
    entry = next(item for item in info.entries if item.name.endswith("测试文件.txt"))
    assert entry.ratio is not None
    assert entry.crc is not None


def test_close_archive_returns_home(window: MainWindow, archive: Path) -> None:
    window.open_archive(archive)
    window.close_archive()
    assert window.current_archive is None
    assert window.stack.currentWidget() is window.home_page
    assert window.address_edit.text() == ""


def test_open_missing_archive_is_safe(window: MainWindow, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QMessageBox

    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: shown.append(str(args[1])))
    window.open_archive(tmp_path / "不存在.zip")
    assert shown
    assert window.current_archive is None


def test_theme_switching(window: MainWindow, qapp) -> None:  # type: ignore[no-untyped-def]
    from app.services.settings import THEME_DARK, THEME_LIGHT, THEME_SYSTEM

    for theme in (THEME_DARK, THEME_LIGHT, THEME_SYSTEM):
        window.settings.update(theme=theme)
        window._on_theme_changed(theme)
        resolved = resolve_theme(theme)
        assert resolved in {"dark", "light"}
    apply_theme(qapp, THEME_LIGHT)
    assert "QMainWindow" in qapp.styleSheet()


def test_compress_via_task_panel(window: MainWindow, tmp_path: Path, sample_tree: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "面板任务.zip"
    window._submit_compress(CreateOptions(target=target, sources=[sample_tree]))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        qtbot.wait(20)
        tasks = window.task_manager.all_tasks()
        if tasks and tasks[0].status.is_final:
            break
    tasks = window.task_manager.all_tasks()
    assert tasks and tasks[0].status is TaskStatus.COMPLETED
    assert target.exists()
    assert window.task_panel.table.rowCount() == 1
    assert "任务：0/1" in window.task_count_label.text()


def test_task_details_dialog(
    window: MainWindow, tmp_path: Path, sample_tree: Path, qtbot, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from app.gui.dialogs import details_dialog

    captured: dict[str, str] = {}

    class FakeDialog:
        def __init__(self, *_args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            captured["text"] = kwargs.get("text", "")
            captured["title"] = kwargs.get("title", "")

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(details_dialog, "DetailsDialog", FakeDialog)
    import app.gui.main_window as main_window_module

    monkeypatch.setattr(main_window_module, "DetailsDialog", FakeDialog)
    target = tmp_path / "详情.zip"
    window._submit_compress(CreateOptions(target=target, sources=[sample_tree]))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        qtbot.wait(20)
        if window.task_manager.all_tasks()[0].status.is_final:
            break
    task_id = window.task_manager.all_tasks()[0].id
    window.show_task_details(task_id)
    assert "任务：" in captured["text"]
    assert "进度：100%" in captured["text"]


def test_drop_archive_opens_it(window: MainWindow, archive: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
    window._handle_dropped_paths([archive])
    qtbot.wait(50)
    assert window.current_archive == archive


def test_volume_archive_browsing(window: MainWindow, tmp_path: Path, sample_tree: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "分卷.zip"
    window.manager.create_archive(CreateOptions(target=target, sources=[sample_tree], volume_size=400))
    first = target.with_name(target.name + ".001")
    window.open_archive(first)
    qtbot.wait(30)
    assert window.current_archive == first
    assert "分卷" in window.archive_view.summary_label.text()
    assert window.archive_view.tree.topLevelItemCount() == 1


def test_settings_and_history_dialogs_construct(window: MainWindow) -> None:
    from app.gui.dialogs.history_dialog import HistoryDialog
    from app.gui.dialogs.settings_dialog import SettingsDialog

    settings_dialog = SettingsDialog(window, settings=window.settings)
    assert settings_dialog.windowTitle() == "设置"
    history_dialog = HistoryDialog(window, history=window.history)
    assert history_dialog.table.rowCount() == 0
    settings_dialog.deleteLater()
    history_dialog.deleteLater()
