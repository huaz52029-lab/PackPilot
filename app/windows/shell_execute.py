"""调用 Windows 默认程序打开文件，并在程序退出后触发回调（用于清理临时文件）。"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


def is_windows() -> bool:
    return os.name == "nt"


def open_with_default_program(path: Path, *, on_exit: Callable[[], None] | None = None) -> bool:
    """使用系统默认程序打开文件。

    返回是否成功启动。Windows 下使用 ``ShellExecuteExW`` 获取进程句柄，
    从而可以在程序退出后回调 ``on_exit``（用于清理临时文件）。
    """

    path = Path(path)
    if not path.exists():
        logger.error("要打开的文件不存在：%s", path)
        return False
    if is_windows():
        return _shell_execute_windows(path, on_exit)
    return _open_fallback(path, on_exit)


def _shell_execute_windows(path: Path, on_exit: Callable[[], None] | None) -> bool:
    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("fMask", ctypes.c_ulong),
            ("hwnd", ctypes.c_void_p),
            ("lpVerb", ctypes.c_wchar_p),
            ("lpFile", ctypes.c_wchar_p),
            ("lpParameters", ctypes.c_wchar_p),
            ("lpDirectory", ctypes.c_wchar_p),
            ("nShow", ctypes.c_int),
            ("hInstApp", ctypes.c_void_p),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", ctypes.c_wchar_p),
            ("hkeyClass", ctypes.c_void_p),
            ("dwHotKey", ctypes.c_ulong),
            ("hIcon", ctypes.c_void_p),
            ("hProcess", ctypes.c_void_p),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SEE_MASK_NOASYNC = 0x00000100
    SW_SHOWNORMAL = 1
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = "open"
    info.lpFile = str(path)
    info.lpDirectory = str(path.parent)
    info.nShow = SW_SHOWNORMAL
    try:
        result = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info))  # type: ignore[attr-defined]
    except Exception as exc:
        logger.error("ShellExecuteEx 调用失败：%s", exc)
        return False
    if not result:
        error = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
        logger.error("ShellExecuteEx 未成功启动默认程序（错误码 %s）：%s", error, path)
        return False
    handle = info.hProcess
    if handle and on_exit is not None:
        threading.Thread(
            target=_wait_for_exit, args=(handle, on_exit), name="packpilot-shell-wait", daemon=True
        ).start()
    return True


def _wait_for_exit(handle: int, on_exit: Callable[[], None]) -> None:
    """等待进程结束，然后执行回调（忽略异常，避免影响主程序）。"""

    SYNCHRONIZE = 0x00100000
    WAIT_TIMEOUT = 0x00000102
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        # 最长等待 24 小时，超时后仍尝试清理
        deadline_seconds = 24 * 3600
        waited = 0
        while waited < deadline_seconds:
            result = kernel32.WaitForSingleObject(handle, 5000)
            if result != WAIT_TIMEOUT:
                break
            waited += 5
        with contextlib.suppress(Exception):
            on_exit()
        with contextlib.suppress(Exception):
            kernel32.CloseHandle(handle)
    except Exception:
        logger.debug("等待默认程序退出失败", exc_info=True)
        with contextlib.suppress(Exception):
            on_exit()
    del SYNCHRONIZE


def _open_fallback(path: Path, on_exit: Callable[[], None] | None) -> bool:
    try:
        if sys.platform == "darwin":
            command = ["open", str(path)]
        else:
            command = ["xdg-open", str(path)]
        process = subprocess.Popen(command)
        if on_exit is not None:
            threading.Thread(
                target=lambda: (process.wait(), on_exit()),
                name="packpilot-open-wait",
                daemon=True,
            ).start()
        return True
    except OSError as exc:
        logger.error("无法使用默认程序打开文件：%s（%s）", path, exc)
        return False


def reveal_in_explorer(path: Path) -> bool:
    """在资源管理器中定位文件（文件不存在时打开其父目录）。"""

    path = Path(path)
    if is_windows():
        target = str(path) if path.exists() else str(path.parent)
        try:
            if path.exists() and path.is_file():
                subprocess.Popen(["explorer", "/select,", target])
            else:
                os.startfile(target)  # type: ignore[attr-defined]
            return True
        except OSError as exc:
            logger.error("无法打开资源管理器：%s", exc)
            return False
    return open_directory(path if path.is_dir() else path.parent)


def open_directory(path: Path) -> bool:
    path = Path(path)
    if not path.exists():
        logger.error("目录不存在：%s", path)
        return False
    if is_windows():
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
            return True
        except OSError as exc:
            logger.error("打开目录失败：%s", exc)
            return False
    return _open_fallback(path, None)


def open_url(url: str) -> bool:
    if is_windows():
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        except Exception:
            try:
                subprocess.Popen(["cmd", "/c", "start", "", url])
                return True
            except OSError:
                return False
    try:
        subprocess.Popen(["xdg-open", url])
        return True
    except OSError:
        return False
