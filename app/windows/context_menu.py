"""Windows 资源管理器右键菜单（HKCU，可完整卸载）。"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.archive_info import ArchiveFormat
from app.version import APP_NAME
from app.windows import registry_utils as reg
from app.windows.file_association import running_executable

logger = logging.getLogger(__name__)

VERB_ROOT = r"Software\Classes\PackPilot.Verbs"
DIRECTORY_KEY = r"Software\Classes\Directory\shell\PackPilot"
DIRECTORY_BACKGROUND_KEY = r"Software\Classes\Directory\Background\shell\PackPilot"
ARCHIVE_EXTENSIONS: tuple[str, ...] = tuple(
    suffix for archive_format in ArchiveFormat for suffix in archive_format.suffixes
)


def _command(arguments: str) -> str:
    return f"{running_executable()} {arguments}"


def _create_verb(name: str, label: str, arguments: str) -> str:
    """创建一个子命令，返回其标识名。"""

    key = VERB_ROOT + "\\" + name
    reg.set_value(key, "MUIVerb", label)
    reg.set_value(key, "Icon", _icon_reference())
    reg.set_value(key + r"\shell\open\command", None, _command(arguments))
    reg.set_value(key + r"\command", None, _command(arguments))
    return name


def _icon_reference() -> str:
    from app.windows.file_association import _icon_path

    return _icon_path()


def _sub_commands_reference(verbs: list[str]) -> str:
    return ";".join(verbs)


def install_context_menu() -> bool:
    """注册文件夹与压缩包的右键菜单。"""

    if not reg.registry_available():
        return False
    folder_verbs = [
        _create_verb("PackPilot.CompressZip", "压缩为 ZIP", '--quick-compress "%1" zip'),
        _create_verb("PackPilot.Compress7z", "压缩为 7Z", '--quick-compress "%1" 7z'),
        _create_verb("PackPilot.CompressAsk", "压缩为…（选择格式）", '--quick-compress-ask "%1"'),
        _create_verb("PackPilot.OpenHere", "使用 PackPilot 打开", '--quick-browse "%1"'),
    ]
    archive_verbs = [
        _create_verb("PackPilot.Open", "使用 PackPilot 打开", '--quick-browse "%1"'),
        _create_verb("PackPilot.ExtractHere", "解压到当前文件夹", '--quick-extract "%1"'),
        _create_verb("PackPilot.ExtractTo", "解压到指定位置…", '--quick-extract-to "%1"'),
        _create_verb("PackPilot.Test", "测试压缩包", '--quick-test "%1"'),
    ]
    for key in (DIRECTORY_KEY, DIRECTORY_BACKGROUND_KEY):
        reg.set_value(key, "MUIVerb", APP_NAME)
        reg.set_value(key, "Icon", _icon_reference())
        reg.set_value(key, "SubCommands", _sub_commands_reference(folder_verbs))

    for suffix in ARCHIVE_EXTENSIONS:
        key = rf"Software\Classes\SystemFileAssociations\{suffix}\shell\PackPilot"
        reg.set_value(key, "MUIVerb", APP_NAME)
        reg.set_value(key, "Icon", _icon_reference())
        reg.set_value(key, "SubCommands", _sub_commands_reference(archive_verbs))
    reg.notify_shell_associations_changed()
    logger.info("已注册右键菜单")
    return True


def remove_context_menu() -> bool:
    """移除全部右键菜单项。"""

    if not reg.registry_available():
        return False
    reg.delete_key(DIRECTORY_KEY)
    reg.delete_key(DIRECTORY_BACKGROUND_KEY)
    for suffix in ARCHIVE_EXTENSIONS:
        key = rf"Software\Classes\SystemFileAssociations\{suffix}\shell\PackPilot"
        reg.delete_key(key)
    reg.delete_key(VERB_ROOT)
    reg.notify_shell_associations_changed()
    logger.info("已移除右键菜单")
    return True


def context_menu_installed() -> bool:
    """判断右键菜单是否已注册。"""

    if not reg.registry_available():
        return False
    return reg.get_value(DIRECTORY_KEY, "SubCommands") is not None


def context_menu_summary() -> str:
    if context_menu_installed():
        return "已安装右键菜单（文件夹压缩、压缩包解压/测试）"
    return "未安装右键菜单"


def preview_commands() -> list[tuple[str, str]]:
    """返回菜单项与实际命令，便于在设置界面展示与自检。"""

    return [
        ("压缩为 ZIP", _command('--quick-compress "%1" zip')),
        ("压缩为 7Z", _command('--quick-compress "%1" 7z')),
        ("使用 PackPilot 打开", _command('--quick-browse "%1"')),
        ("解压到当前文件夹", _command('--quick-extract "%1"')),
        ("解压到指定位置…", _command('--quick-extract-to "%1"')),
        ("测试压缩包", _command('--quick-test "%1"')),
    ]


def explorer_restart_hint() -> str:
    return "如菜单未立即出现，请注销后重新登录，或在任务管理器中重启“Windows 资源管理器”。"


def folder_path_example() -> Path:
    return Path.home() / "Documents"
