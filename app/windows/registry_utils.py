"""注册表工具（仅使用 HKEY_CURRENT_USER，不需要管理员权限）。"""

from __future__ import annotations

import contextlib
import logging
import os

logger = logging.getLogger(__name__)


def registry_available() -> bool:
    return os.name == "nt"


def _winreg():  # type: ignore[no-untyped-def]
    import winreg

    return winreg


def set_value(key_path: str, name: str | None, value: str, *, root: object = None) -> bool:
    """写入（或创建）一个字符串值。"""

    if not registry_available():
        logger.warning("当前系统不是 Windows，跳过注册表写入：%s", key_path)
        return False
    winreg = _winreg()
    hive = root if root is not None else winreg.HKEY_CURRENT_USER
    try:
        with winreg.CreateKeyEx(hive, key_path, 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        return True
    except OSError as exc:
        logger.error("写入注册表失败 %s：%s", key_path, exc)
        return False


def get_value(key_path: str, name: str | None, *, root: object = None) -> str | None:
    """读取字符串值，不存在时返回 ``None``。"""

    if not registry_available():
        return None
    winreg = _winreg()
    hive = root if root is not None else winreg.HKEY_CURRENT_USER
    try:
        with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return str(value) if value is not None else None


def delete_value(key_path: str, name: str | None, *, root: object = None) -> bool:
    if not registry_available():
        return False
    winreg = _winreg()
    hive = root if root is not None else winreg.HKEY_CURRENT_USER
    try:
        with winreg.OpenKey(hive, key_path, 0, winreg.KEY_WRITE) as key:
            winreg.DeleteValue(key, name)
        return True
    except OSError:
        return False


def delete_key(key_path: str, *, root: object = None, recursive: bool = True) -> bool:
    """删除注册表键（默认递归），键不存在时视为成功。"""

    if not registry_available():
        return False
    winreg = _winreg()
    hive = root if root is not None else winreg.HKEY_CURRENT_USER

    def _delete(path: str) -> None:
        subkeys: list[str] = []
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
                index = 0
                while True:
                    try:
                        subkeys.append(winreg.EnumKey(key, index))
                    except OSError:
                        break
                    index += 1
        except FileNotFoundError:
            return
        for subkey in subkeys:
            if recursive:
                _delete(f"{path}\\{subkey}")
            else:
                with contextlib.suppress(OSError):
                    winreg.DeleteKey(hive, f"{path}\\{subkey}")
        with contextlib.suppress(FileNotFoundError):
            winreg.DeleteKey(hive, path)

    try:
        _delete(key_path)
        return True
    except OSError as exc:
        logger.error("删除注册表键失败 %s：%s", key_path, exc)
        return False


def notify_shell_associations_changed() -> None:
    """通知资源管理器刷新文件关联与图标缓存。"""

    if not registry_available():
        return
    try:
        import ctypes

        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST = 0x0000
        ctypes.windll.shell32.SHChangeNotify(  # type: ignore[attr-defined]
            SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None
        )
    except Exception:
        logger.debug("SHChangeNotify 调用失败", exc_info=True)
