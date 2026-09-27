"""Windows 文件关联（当前用户级别注册表，可完整卸载）。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from app.core.archive_info import ArchiveFormat
from app.version import APP_NAME, EXECUTABLE_NAME
from app.windows import registry_utils as reg

logger = logging.getLogger(__name__)

PROG_ID = "PackPilot.Archive"
BACKUP_ROOT = r"Software\PackPilot\Backup\Extensions"
SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(
    suffix for archive_format in ArchiveFormat for suffix in archive_format.suffixes
)


def running_executable() -> str:
    """返回用于文件关联的命令行目标（打包后为 EXE，开发环境为 pythonw + 脚本）。"""

    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}"'
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else python
    project_root = Path(__file__).resolve().parents[2]
    return f'"{interpreter}" "{project_root / "app" / "main.py"}"'


def _command_for(arguments: str) -> str:
    return f"{running_executable()} {arguments}"


def _extension_class_key(suffix: str) -> str:
    return rf"Software\Classes\{suffix}"


def _prog_id_key(prog_id: str = PROG_ID) -> str:
    return rf"Software\Classes\{prog_id}"


def install_file_associations(*, set_default: bool = True) -> list[str]:
    """为受支持扩展名注册 PackPilot（返回成功注册的扩展名列表）。"""

    if not reg.registry_available():
        return []
    registered: list[str] = []
    open_command = _command_for('"%1"')
    reg.set_value(_prog_id_key(), None, "PackPilot 压缩包")
    reg.set_value(_prog_id_key(), "FriendlyTypeName", "PackPilot 压缩包")
    reg.set_value(_prog_id_key() + r"\DefaultIcon", None, _icon_path())
    reg.set_value(_prog_id_key() + r"\shell\open", "MUIVerb", "使用 PackPilot 打开")
    reg.set_value(_prog_id_key() + r"\shell\open\command", None, open_command)
    reg.set_value(_prog_id_key() + r"\shell\extract", "MUIVerb", "解压到当前文件夹")
    reg.set_value(
        _prog_id_key() + r"\shell\extract\command",
        None,
        _command_for('--quick-extract "%1"'),
    )

    for suffix in SUPPORTED_EXTENSIONS:
        key = _extension_class_key(suffix)
        per_extension_prog_id = f"{PROG_ID}{suffix}"
        reg.set_value(rf"Software\Classes\{per_extension_prog_id}", None, "PackPilot 压缩包")
        reg.set_value(rf"Software\Classes\{per_extension_prog_id}\DefaultIcon", None, _icon_path())
        reg.set_value(rf"Software\Classes\{per_extension_prog_id}\shell\open\command", None, open_command)
        reg.set_value(key + r"\OpenWithProgids", per_extension_prog_id, "")
        reg.set_value(key + r"\OpenWithProgids", PROG_ID, "")
        if set_default:
            previous = reg.get_value(key, None)
            backup_key = BACKUP_ROOT + "\\" + suffix.lstrip(".")
            if previous and previous != per_extension_prog_id:
                reg.set_value(backup_key, "previous", previous)
            elif previous is None:
                reg.set_value(backup_key, "previous", "__none__")
            reg.set_value(key, None, per_extension_prog_id)
        registered.append(suffix)

    _register_capabilities()
    reg.notify_shell_associations_changed()
    logger.info("已注册文件关联：%s", "、".join(registered))
    return registered


def _register_capabilities() -> None:
    capabilities = r"Software\PackPilot\Capabilities"
    reg.set_value(capabilities, "ApplicationName", APP_NAME)
    reg.set_value(capabilities, "ApplicationDescription", "Windows 轻量级压缩包管理器")
    for suffix in SUPPORTED_EXTENSIONS:
        reg.set_value(capabilities + r"\FileAssociations", suffix, f"{PROG_ID}{suffix}")
    reg.set_value(r"Software\RegisteredApplications", APP_NAME, r"Software\PackPilot\Capabilities")


def remove_file_associations() -> list[str]:
    """移除 PackPilot 建立的关联，并尽量恢复原有默认程序。"""

    if not reg.registry_available():
        return []
    removed: list[str] = []
    for suffix in SUPPORTED_EXTENSIONS:
        key = _extension_class_key(suffix)
        per_extension_prog_id = f"{PROG_ID}{suffix}"
        reg.delete_value(key + r"\OpenWithProgids", per_extension_prog_id)
        reg.delete_value(key + r"\OpenWithProgids", PROG_ID)
        reg.delete_key(rf"Software\Classes\{per_extension_prog_id}")
        previous = reg.get_value(BACKUP_ROOT + "\\" + suffix.lstrip("."), "previous")
        current = reg.get_value(key, None)
        if current in {per_extension_prog_id, PROG_ID}:
            if previous and previous != "__none__":
                reg.set_value(key, None, previous)
            else:
                reg.delete_value(key, None)
        reg.delete_key(BACKUP_ROOT + "\\" + suffix.lstrip("."))
        removed.append(suffix)

    reg.delete_key(r"Software\Classes\PackPilot.Archive")
    reg.delete_value(r"Software\RegisteredApplications", APP_NAME)
    reg.delete_key(r"Software\PackPilot\Capabilities")
    reg.notify_shell_associations_changed()
    logger.info("已移除文件关联：%s", "、".join(removed))
    return removed


def association_status() -> dict[str, bool]:
    """返回每个扩展名当前是否已注册 PackPilot。"""

    if not reg.registry_available():
        return dict.fromkeys(SUPPORTED_EXTENSIONS, False)
    status: dict[str, bool] = {}
    for suffix in SUPPORTED_EXTENSIONS:
        current = reg.get_value(_extension_class_key(suffix), None) or ""
        if current.startswith(PROG_ID):
            status[suffix] = True
            continue
        open_with = reg.get_value(
            _extension_class_key(suffix) + r"\OpenWithProgids",
            f"{PROG_ID}{suffix}",
        )
        status[suffix] = open_with is not None
    return status


def association_summary() -> str:
    status = association_status()
    active = [suffix for suffix, ok in status.items() if ok]
    if not active:
        return "未安装文件关联"
    return f"已关联 {len(active)} 个扩展名：" + "、".join(active)


def _icon_path() -> str:
    if getattr(sys, "frozen", False):
        return f"{Path(sys.executable)},0"
    resource = Path(__file__).resolve().parents[2] / "resources" / "icons" / "packpilot.ico"
    if resource.exists():
        return f"{resource},0"
    return f"{Path(sys.executable)},0"


def registration_hint() -> str:
    """返回可在命令行手动执行关联的参数提示。"""

    exe = EXECUTABLE_NAME if getattr(sys, "frozen", False) else "python -m app.main"
    return f"{exe} --install-associations"
