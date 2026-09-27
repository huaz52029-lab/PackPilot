"""PyInstaller 运行时钩子：把 PySide6 / shiboken6 的 DLL 目录加入搜索路径。

PySide6 6.11 把 ``shiboken6.abi3.dll`` 放在独立的 ``shiboken6`` 目录中，
打包后该目录不在默认 DLL 搜索路径内，会导致 ``ImportError: DLL load failed
while importing QtCore``。此钩子在应用代码导入 PySide6 之前注册目录。
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path


def _register(directory: Path) -> None:
    if not directory.is_dir():
        return
    if hasattr(os, "add_dll_directory"):
        with contextlib.suppress(OSError):
            os.add_dll_directory(str(directory))
    text = str(directory)
    if text not in sys.path:
        sys.path.insert(0, text)
    path_env = os.environ.get("PATH", "")
    if text not in path_env.split(os.pathsep):
        os.environ["PATH"] = text + os.pathsep + path_env


def _bootstrap() -> None:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    for name in ("shiboken6", "PySide6", "."):
        _register((base / name).resolve())


_bootstrap()
