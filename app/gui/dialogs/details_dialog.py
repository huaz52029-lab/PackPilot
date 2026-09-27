"""通用详情对话框（任务结果、条目属性、扫描结果）。"""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class DetailsDialog(QDialog):
    """展示只读文本，支持复制全部内容。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "详情",
        text: str = "",
        width: int = 680,
        height: int = 460,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(width, height)
        self.editor = QPlainTextEdit(self)
        self.editor.setReadOnly(True)
        self.editor.setPlainText(text)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        copy_button = QPushButton("复制全部", self)
        copy_button.clicked.connect(self._copy_all)
        buttons.addButton(copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.editor)
        layout.addWidget(buttons)

    def set_text(self, text: str) -> None:
        self.editor.setPlainText(text)

    def append_text(self, text: str) -> None:
        self.editor.appendPlainText(text)

    def _copy_all(self) -> None:
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(self.editor.toPlainText())
