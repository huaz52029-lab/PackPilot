"""从 ``app/version.py`` 生成安装程序所需的版本文件。

生成：

* ``installer/version.iss``        —— Inno Setup 版本定义（唯一版本来源同步）
* ``installer/version_info.txt``   —— PyInstaller/PE 版本资源
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.version import APP_NAME, COPYRIGHT, ORGANIZATION, __version__  # noqa: E402

INSTALLER_DIR = PROJECT_ROOT / "installer"
VERSION_ISS = INSTALLER_DIR / "version.iss"
VERSION_INFO = INSTALLER_DIR / "version_info.txt"


def build_version_iss() -> str:
    return f'; 由 scripts/make_version_info.py 自动生成，请勿手工修改\n#define MyAppVersion "{__version__}"\n'


def build_version_info() -> str:
    parts = [int(part) for part in __version__.split(".")]
    while len(parts) < 4:
        parts.append(0)
    version_tuple = ", ".join(str(part) for part in parts)
    return (
        "VSVersionInfo(\n"
        "  ffi=FixedFileInfo(\n"
        f"    filevers=({version_tuple}),\n"
        f"    prodvers=({version_tuple}),\n"
        "    mask=0x3f,\n"
        "    flags=0x0,\n"
        "    OS=0x40004,\n"
        "    fileType=0x1,\n"
        "    subtype=0x0,\n"
        "    date=(0, 0)\n"
        "  ),\n"
        "  kids=[\n"
        "    StringFileInfo([\n"
        "      StringTable(\n"
        "        '080404b0',\n"
        f"        [StringStruct('CompanyName', '{ORGANIZATION}'),\n"
        f"         StringStruct('FileDescription', '{APP_NAME} 压缩包管理器'),\n"
        f"         StringStruct('FileVersion', '{__version__}'),\n"
        f"         StringStruct('InternalName', '{APP_NAME}'),\n"
        f"         StringStruct('LegalCopyright', '{COPYRIGHT}'),\n"
        f"         StringStruct('OriginalFilename', '{APP_NAME}.exe'),\n"
        f"         StringStruct('ProductName', '{APP_NAME}'),\n"
        f"         StringStruct('ProductVersion', '{__version__}')])\n"
        "    ]),\n"
        "    VarFileInfo([VarStruct('Translation', [2052, 1200])])\n"
        "  ]\n"
        ")\n"
    )


def main() -> int:
    INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    VERSION_ISS.write_text(build_version_iss(), encoding="utf-8", newline="\n")
    VERSION_INFO.write_text(build_version_info(), encoding="utf-8", newline="\n")
    print(f"已生成 {VERSION_ISS.name} 与 {VERSION_INFO.name}（版本 {__version__}）")
    return 0


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
