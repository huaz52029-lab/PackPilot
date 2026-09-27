# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（Windows GUI 模式，onedir）。

使用方式：

    pyinstaller PackPilot.spec --noconfirm

版本号统一来自 app/version.py，禁止在此处硬编码其它版本。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH)  # type: ignore[name-defined]
sys.path.insert(0, str(PROJECT_ROOT))

from app.version import APP_NAME, __version__  # noqa: E402

# 某些开发环境会把无关的本地依赖目录加入 PATH（例如 Codex 运行时的 poppler/libheif），
# PyInstaller 在解析 Qt 依赖时可能误收集其中的 icuuc.dll / ucrtbase.dll 等不兼容副本，
# 导致运行期 "DLL load failed while importing QtCore"。这里显式过滤这些外来二进制。
FOREIGN_BINARY_MARKERS = ("codex-runtimes", "\\poppler\\", "\\libheif\\")


def _is_foreign_binary(entry: tuple) -> bool:  # type: ignore[type-arg]
    source = str(entry[1]).lower()
    return any(marker in source for marker in FOREIGN_BINARY_MARKERS)

EXCLUDES = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtSpatialAudio",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtTest",
    "PySide6.QtSql",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtTextToSpeech",
    "PySide6.QtOpenGLFunctions",
    "tkinter",
    "unittest",
]

HIDDEN_IMPORTS = [
    "py7zr",
    "py7zr.callbacks",
    "py7zr.io",
    "py7zr.exceptions",
    "multivolumefile",
    "brotli",
    "pyppmd",
    "pybcj",
    "inflate64",
    "psutil",
    "Cryptodome",
    "backports.zstd",
    "app.gui.dialogs.new_archive_dialog",
    "app.gui.dialogs.extract_dialog",
    "app.gui.dialogs.convert_dialog",
    "app.gui.dialogs.hash_dialog",
    "app.gui.dialogs.history_dialog",
    "app.gui.dialogs.settings_dialog",
    "app.gui.dialogs.about_dialog",
    "app.gui.dialogs.details_dialog",
    "app.gui.dialogs.password_dialog",
]

datas = [
    (str(PROJECT_ROOT / "resources"), "resources"),
    (str(PROJECT_ROOT / "README.md"), "."),
    (str(PROJECT_ROOT / "LICENSE"), "."),
]

icon_path = PROJECT_ROOT / "resources" / "icons" / "packpilot.ico"

analysis = Analysis(  # type: ignore[name-defined]
    [str(PROJECT_ROOT / "app" / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

_foreign = [entry for entry in analysis.binaries if _is_foreign_binary(entry)]
if _foreign:
    print(f"[PackPilot] 过滤 {len(_foreign)} 个外来依赖 DLL：")
    for entry in _foreign:
        print(f"    - {entry[0]}  <-  {entry[1]}")
analysis.binaries = [entry for entry in analysis.binaries if not _is_foreign_binary(entry)]

pyz = PYZ(analysis.pure)  # type: ignore[name-defined]

executable = EXE(  # type: ignore[name-defined]
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # Windows GUI 模式（--version 通过附加控制台输出）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path.exists() else None,
    version=str(PROJECT_ROOT / "installer" / "version_info.txt")
    if (PROJECT_ROOT / "installer" / "version_info.txt").exists()
    else None,
)

coll = COLLECT(  # type: ignore[name-defined]
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
