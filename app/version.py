"""版本与产品信息的唯一来源。

项目中所有版本号（GUI 标题、CLI、打包脚本、安装程序）都必须从这里读取，
禁止在其它位置硬编码版本字符串。
"""

from __future__ import annotations

APP_NAME = "PackPilot"
APP_ID = "PackPilot"
REPO_OWNER = "huaz52029-lab"
REPO_NAME = "PackPilot"
REPO_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
ISSUES_URL = f"{REPO_URL}/issues"

__version__ = "1.0.0"
VERSION_TUPLE = tuple(int(part) for part in __version__.split("."))
VERSION_DISPLAY = f"{APP_NAME} {__version__}"

COPYRIGHT = "Copyright (c) 2026 PackPilot contributors"
LICENSE_NAME = "MIT"
ORGANIZATION = "PackPilot"

# 可执行文件名（PyInstaller / Inno Setup / 注册表关联共用）
EXECUTABLE_NAME = "PackPilot.exe"
CLI_EXECUTABLE_NAME = "packpilot.exe"


def version_string() -> str:
    """返回 ``PackPilot 1.0.0`` 形式的版本字符串。"""

    return VERSION_DISPLAY
