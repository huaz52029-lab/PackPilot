"""Windows 集成测试：文件关联、右键菜单、命令拼装与临时文件清理。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from app.services.temp_manager import TempManager
from app.windows import context_menu, file_association
from app.windows import registry_utils as reg


def test_running_executable_contains_interpreter_or_exe() -> None:
    command = file_association.running_executable()
    assert command
    if getattr(sys, "frozen", False):
        assert command.lower().endswith('.exe"')
    else:
        assert "python" in command.lower()
        assert "main.py" in command


def test_supported_extensions_cover_all_formats() -> None:
    from app.core.archive_info import ArchiveFormat

    for archive_format in ArchiveFormat:
        for suffix in archive_format.suffixes:
            assert suffix in file_association.SUPPORTED_EXTENSIONS


def test_context_menu_preview_commands() -> None:
    commands = dict(context_menu.preview_commands())
    assert "压缩为 ZIP" in commands
    assert "解压到当前文件夹" in commands
    assert "--quick-compress" in commands["压缩为 ZIP"]
    assert "--quick-extract" in commands["解压到当前文件夹"]
    assert "%1" in commands["压缩为 ZIP"]


def test_registry_helpers_on_current_platform() -> None:
    if os.name != "nt":
        pytest.skip("仅 Windows 支持注册表")
    assert reg.registry_available() is True
    key = r"Software\PackPilot\TestKey"
    assert reg.set_value(key, "name", "值")
    assert reg.get_value(key, "name") == "值"
    assert reg.delete_key(key)
    assert reg.get_value(key, "name") is None


@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 支持注册表")
def test_file_association_install_and_remove_roundtrip() -> None:
    """真实写入并清理 HKCU 注册表（不修改默认打开方式，只加入“打开方式”列表）。"""

    original = {
        suffix: reg.get_value(rf"Software\Classes\{suffix}", None)
        for suffix in file_association.SUPPORTED_EXTENSIONS
    }
    try:
        registered = file_association.install_file_associations(set_default=False)
        assert registered
        status = file_association.association_status()
        assert status[".zip"] is True
        assert (
            file_association.PROG_ID + ".zip"
            in (
                reg.get_value(r"Software\Classes\.zip\OpenWithProgids", file_association.PROG_ID + ".zip")
                or ""
            )
            or reg.get_value(r"Software\Classes\.zip\OpenWithProgids", file_association.PROG_ID + ".zip")
            is not None
        )

        removed = file_association.remove_file_associations()
        assert removed
        assert file_association.association_status()[".zip"] is False
        assert (
            reg.get_value(r"Software\Classes\.zip\OpenWithProgids", file_association.PROG_ID + ".zip") is None
        )
    finally:
        file_association.remove_file_associations()
        for suffix, value in original.items():
            key = rf"Software\Classes\{suffix}"
            if value:
                reg.set_value(key, None, value)


@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 支持注册表")
def test_context_menu_install_and_remove_roundtrip() -> None:
    try:
        assert context_menu.install_context_menu() is True
        assert context_menu.context_menu_installed() is True
        assert reg.get_value(context_menu.DIRECTORY_KEY, "SubCommands")
        assert reg.get_value(r"Software\Classes\Directory\shell\PackPilot", "MUIVerb") == "PackPilot"
    finally:
        assert context_menu.remove_context_menu() is True
    assert context_menu.context_menu_installed() is False


def test_executable_hint_text() -> None:
    hint = file_association.registration_hint()
    assert "--install-associations" in hint
    assert "资源管理器" in context_menu.explorer_restart_hint()


def test_temp_manager_creates_and_cleans(tmp_path: Path, qapp) -> None:  # type: ignore[no-untyped-def]
    manager = TempManager(tmp_path)
    workspace = manager.create_workspace("open")
    assert workspace.exists()
    extracted = workspace / "文件.txt"
    extracted.write_text("内容", encoding="utf-8")

    assert manager.cleanup_now(workspace) is True
    assert not workspace.exists()

    stale = manager.create_workspace("stale")
    (stale / "old.txt").write_text("x", encoding="utf-8")
    removed = manager.cleanup_stale(max_age_hours=0)
    assert removed >= 1
    assert not stale.exists()


def test_temp_manager_schedules_cleanup(tmp_path: Path, qapp) -> None:  # type: ignore[no-untyped-def]
    manager = TempManager(tmp_path)
    workspace = manager.create_workspace("open")
    (workspace / "文件.txt").write_text("内容", encoding="utf-8")
    manager.schedule_cleanup(workspace, initial_delay_ms=0, interval_ms=50, max_attempts=5)
    deadline = 200
    for _ in range(deadline):
        qapp.processEvents()
        if not workspace.exists():
            break
        qapp.thread().msleep(10)
    assert not workspace.exists()


def test_shell_execute_missing_file(tmp_path: Path) -> None:
    from app.windows.shell_execute import open_with_default_program

    assert open_with_default_program(tmp_path / "不存在.txt") is False
