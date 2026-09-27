"""对话框构造测试：确保各中文对话框可以正常创建并返回正确配置。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.archive_info import ArchiveFormat
from app.core.archive_manager import ArchiveManager
from app.core.options import CompressionLevel, CreateOptions, OverwritePolicy
from app.gui.dialogs.about_dialog import AboutDialog
from app.gui.dialogs.convert_dialog import ConvertDialog
from app.gui.dialogs.details_dialog import DetailsDialog
from app.gui.dialogs.extract_dialog import BatchExtractDialog, ExtractDialog
from app.gui.dialogs.hash_dialog import HashDialog
from app.gui.dialogs.new_archive_dialog import BatchCompressDialog, NewArchiveDialog
from app.gui.dialogs.password_dialog import PasswordDialog
from app.services.settings import SettingsService
from app.tasks.task_manager import TaskManager


@pytest.fixture
def settings(tmp_path: Path) -> SettingsService:
    return SettingsService(tmp_path / "settings.json")


@pytest.fixture
def archive_info(tmp_path: Path, sample_tree: Path):  # type: ignore[no-untyped-def]
    archive = tmp_path / "示例.zip"
    manager = ArchiveManager()
    manager.create_archive(CreateOptions(target=archive, sources=[sample_tree]))
    return manager.list_archive(archive)


def test_new_archive_dialog_returns_options(
    qtbot, tmp_path: Path, sample_tree: Path, settings: SettingsService
) -> None:  # type: ignore[no-untyped-def]
    dialog = NewArchiveDialog(None, settings=settings, initial_sources=[sample_tree])
    qtbot.addWidget(dialog)
    options = dialog.create_options()
    assert options.sources == [sample_tree]
    assert options.resolved_format() is ArchiveFormat.ZIP
    assert options.target.suffix == ".zip"
    assert options.level is CompressionLevel.NORMAL

    dialog.format_combo.setCurrentIndex(dialog.format_combo.findData("7z"))
    dialog.name_edit.setText("我的压缩包")
    dialog.password_check.setChecked(True)
    dialog.password_edit.setText("Str0ng!Pass")
    dialog.confirm_edit.setText("Str0ng!Pass")
    options = dialog.create_options()
    assert options.resolved_format() is ArchiveFormat.SEVEN_ZIP
    assert options.target.name == "我的压缩包.7z"
    assert options.password == "Str0ng!Pass"


def test_new_archive_dialog_disables_password_for_zip(
    qtbot, tmp_path: Path, sample_tree: Path, settings: SettingsService
) -> None:  # type: ignore[no-untyped-def]
    dialog = NewArchiveDialog(None, settings=settings, initial_sources=[sample_tree])
    qtbot.addWidget(dialog)
    dialog.format_combo.setCurrentIndex(dialog.format_combo.findData("zip"))
    assert not dialog.password_check.isEnabled()
    assert "zipfile" in dialog.password_note.text()
    dialog.format_combo.setCurrentIndex(dialog.format_combo.findData("7z"))
    assert dialog.password_check.isEnabled()


def test_new_archive_dialog_volume_options(qtbot, sample_tree: Path, settings: SettingsService) -> None:  # type: ignore[no-untyped-def]
    dialog = NewArchiveDialog(None, settings=settings, initial_sources=[sample_tree])
    qtbot.addWidget(dialog)
    labels = [dialog.volume_combo.itemText(i) for i in range(dialog.volume_combo.count())]
    assert "100 MB" in labels and "1 GB" in labels and "自定义" in labels
    dialog.volume_combo.setCurrentIndex(labels.index("100 MB"))
    assert dialog.create_options().volume_size == 100 * 1024 * 1024
    dialog.volume_combo.setCurrentIndex(labels.index("自定义"))
    dialog.custom_volume_spin.setValue(50)
    assert dialog.create_options().volume_size == 50 * 1024 * 1024


def test_batch_compress_dialog_modes(
    qtbot, tmp_path: Path, sample_tree: Path, settings: SettingsService
) -> None:  # type: ignore[no-untyped-def]
    dialog = BatchCompressDialog(None, settings=settings, folders=[sample_tree])
    qtbot.addWidget(dialog)
    assert dialog.folders() == [sample_tree]
    assert dialog.merge_mode() is False
    dialog.merge_radio.setChecked(True)
    assert dialog.merge_mode() is True
    assert dialog.merge_name_edit.isEnabled()
    assert dialog.template().sources == [sample_tree]


def test_extract_dialog_defaults_to_smart_target(
    qtbot, tmp_path: Path, archive_info, settings: SettingsService
) -> None:  # type: ignore[no-untyped-def]
    dialog = ExtractDialog(None, info=archive_info, settings=settings)
    qtbot.addWidget(dialog)
    options = dialog.options()
    assert options.target == tmp_path
    assert options.smart is True
    assert options.policy is OverwritePolicy.RENAME
    assert "需要空间" in dialog.space_label.text()
    assert dialog.file_count == archive_info.file_count


def test_extract_dialog_selected_entries(
    qtbot, tmp_path: Path, archive_info, settings: SettingsService
) -> None:  # type: ignore[no-untyped-def]
    dialog = ExtractDialog(None, info=archive_info, settings=settings, entries=["测试项目/中文 文件夹"])
    qtbot.addWidget(dialog)
    assert dialog.options().entries == ["测试项目/中文 文件夹"]
    assert dialog.required_bytes > 0


def test_batch_extract_dialog(qtbot, tmp_path: Path, settings: SettingsService) -> None:  # type: ignore[no-untyped-def]
    archives = [tmp_path / "a.zip", tmp_path / "b.7z"]
    dialog = BatchExtractDialog(None, settings=settings, archives=archives)
    qtbot.addWidget(dialog)
    assert dialog.archives() == archives
    assert dialog.subdir_check.isChecked() is True
    assert dialog.options_template().policy is OverwritePolicy.RENAME


def test_convert_dialog_defaults(qtbot, tmp_path: Path, archive_info, settings: SettingsService) -> None:  # type: ignore[no-untyped-def]
    dialog = ConvertDialog(None, source=archive_info.path, settings=settings, encrypted=False)
    qtbot.addWidget(dialog)
    options = dialog.options()
    assert options.source == archive_info.path
    assert options.target.name == "示例.7z"
    assert options.resolved_format() is ArchiveFormat.SEVEN_ZIP
    dialog.format_combo.setCurrentIndex(dialog.format_combo.findData("zip"))
    dialog._on_format_changed()
    assert dialog.options().target.name == "示例.zip"
    assert not dialog.password_edit.isEnabled()


def test_hash_dialog_result_rendering(qtbot, tmp_path: Path, settings: SettingsService) -> None:  # type: ignore[no-untyped-def]
    from app.core.checksum import HashResult

    task_manager = TaskManager()
    dialog = HashDialog(None, manager=task_manager, settings=settings, paths=[tmp_path / "a.txt"])
    qtbot.addWidget(dialog)
    assert dialog.paths() == [tmp_path / "a.txt"]
    results = [HashResult(path=tmp_path / "a.txt", algorithm="sha256", digest="a" * 64, size=10)]
    dialog._show_results(results)
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 1).text() == "SHA-256"
    assert dialog.copy_all_button.isEnabled()


def test_password_and_details_and_about_dialogs(qtbot) -> None:  # type: ignore[no-untyped-def]
    password_dialog = PasswordDialog(None, with_strength=True, confirm=True)
    qtbot.addWidget(password_dialog)
    password_dialog.password_edit.setText("abc")
    password_dialog.confirm_edit.setText("abc")
    password_dialog._on_accept()
    assert password_dialog.password() == "abc"

    details = DetailsDialog(None, title="详情", text="内容")
    qtbot.addWidget(details)
    assert details.editor.toPlainText() == "内容"

    about = AboutDialog(None)
    qtbot.addWidget(about)
    assert "PackPilot" in about.windowTitle()


def test_password_strength_widget(qtbot) -> None:  # type: ignore[no-untyped-def]
    from app.gui.widgets.password_strength import evaluate_password

    assert evaluate_password("123456").level == "弱"
    assert evaluate_password("Str0ng!Passw0rd2026").level == "强"
    assert evaluate_password("abcdef12345").level == "中"
    # 包含常见弱口令片段会被降级
    assert evaluate_password("Abc12345").level == "弱"
    assert evaluate_password("").level == "弱"
