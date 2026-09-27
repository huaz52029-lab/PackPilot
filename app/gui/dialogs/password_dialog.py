"""密码输入对话框（用于打开加密压缩包）。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from app.gui.widgets.password_strength import PasswordStrengthWidget


class PasswordDialog(QDialog):
    """输入单个密码；``with_strength`` 用于设置新密码时显示强度。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "输入密码",
        description: str = "该压缩包已加密，请输入密码。",
        with_strength: bool = False,
        confirm: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self._confirm = confirm
        self._strength_widget: PasswordStrengthWidget | None = None

        hint = QLabel(description, self)
        hint.setWordWrap(True)
        self.password_edit = QLineEdit(self)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("密码")
        show_box = QCheckBox("显示密码", self)
        show_box.toggled.connect(self._toggle_echo)
        self.confirm_edit: QLineEdit | None = None

        form = QFormLayout()
        form.addRow("密码：", self.password_edit)
        if confirm:
            self.confirm_edit = QLineEdit(self)
            self.confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.confirm_edit.setPlaceholderText("再次输入密码")
            form.addRow("确认密码：", self.confirm_edit)
        form.addRow("", show_box)

        self.error_label = QLabel("", self)
        self.error_label.setStyleSheet("color: #dc2626;")
        self.error_label.setVisible(False)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        self._buttons = buttons

        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addLayout(form)
        if with_strength:
            self._strength_widget = PasswordStrengthWidget(self)
            layout.addWidget(self._strength_widget)
            self.password_edit.textChanged.connect(self._strength_widget.set_password)
        layout.addWidget(self.error_label)
        layout.addWidget(buttons)
        self.password_edit.setFocus()

    def _toggle_echo(self, checked: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self.password_edit.setEchoMode(mode)
        if self.confirm_edit is not None:
            self.confirm_edit.setEchoMode(mode)

    def _on_accept(self) -> None:
        if not self.password_edit.text():
            self._show_error("请输入密码")
            return
        if (
            self._confirm
            and self.confirm_edit is not None
            and self.password_edit.text() != self.confirm_edit.text()
        ):
            self._show_error("两次输入的密码不一致")
            return
        self.accept()

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(True)

    def password(self) -> str:
        return self.password_edit.text()

    @staticmethod
    def ask(
        parent: QWidget | None,
        *,
        title: str = "输入密码",
        description: str = "该压缩包已加密，请输入密码。",
    ) -> str | None:
        dialog = PasswordDialog(parent, title=title, description=description)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.password()
        return None
