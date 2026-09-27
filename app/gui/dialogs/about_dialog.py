"""关于对话框。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.core.archive_info import ArchiveFormat
from app.version import COPYRIGHT, LICENSE_NAME, REPO_URL, VERSION_DISPLAY, __version__


class AboutDialog(QDialog):
    """显示版本、支持格式与项目地址。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("关于 PackPilot")
        self.setMinimumWidth(460)
        title = QLabel(VERSION_DISPLAY, self)
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        formats = "、".join(archive_format.display_name for archive_format in ArchiveFormat)
        description = QLabel(
            "Windows 轻量级压缩包管理器，支持压缩、解压、浏览、测试、转换、批量操作、"
            "密码保护、分卷与 Windows 系统集成。\n\n"
            f"支持格式：{formats}\n"
            "核心依赖：Python 标准库 zipfile / tarfile + py7zr + PySide6\n\n"
            f"{COPYRIGHT}\n"
            f"许可证：{LICENSE_NAME}\n"
            f"项目地址：{REPO_URL}",
            self,
        )
        description.setWordWrap(True)
        description.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        version_note = QLabel(f"内部版本号：{__version__}", self)
        version_note.setStyleSheet("color: #6b7280;")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addWidget(version_note)
        layout.addWidget(buttons)
