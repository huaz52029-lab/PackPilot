"""格式转换对话框。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.archive_info import ArchiveFormat
from app.core.options import ConvertOptions
from app.gui.dialogs.common import (
    VOLUME_OPTIONS,
    fill_format_combo,
    fill_level_combo,
    selected_format,
    selected_level,
    selected_volume_size,
)
from app.services.settings import SettingsService


class ConvertDialog(QDialog):
    """选择目标格式与输出路径，源文件不会被删除。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        source: Path,
        settings: SettingsService,
        encrypted: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("转换压缩格式")
        self.resize(600, 420)
        self.source = Path(source)
        self.settings = settings

        source_box = QGroupBox("源压缩包", self)
        source_form = QFormLayout(source_box)
        source_form.addRow("文件：", QLabel(str(self.source), self))
        detected = ArchiveFormat.from_path(self.source)
        source_form.addRow("当前格式：", QLabel(detected.display_name if detected else "未知", self))
        self.source_password_edit = QLineEdit(self)
        self.source_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.source_password_edit.setPlaceholderText("源压缩包未加密时留空")
        self.source_password_edit.setEnabled(encrypted)
        source_form.addRow("源密码：", self.source_password_edit)

        target_box = QGroupBox("目标压缩包", self)
        self.target_edit = QLineEdit(self)
        self.target_edit.setText(str(self._default_target()))
        browse_button = QPushButton("浏览…", self)
        browse_button.clicked.connect(self._choose_target)
        target_row = QHBoxLayout()
        target_row.addWidget(self.target_edit, 1)
        target_row.addWidget(browse_button)
        self.format_combo = QComboBox(self)
        default_format = (
            ArchiveFormat.SEVEN_ZIP if detected is not ArchiveFormat.SEVEN_ZIP else ArchiveFormat.ZIP
        )
        fill_format_combo(self.format_combo, current=default_format)
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        self.level_combo = QComboBox(self)
        fill_level_combo(self.level_combo, current=settings.default_level)
        self.password_edit = QLineEdit(self)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("可选，仅 7Z 支持写入密码")
        self.confirm_edit = QLineEdit(self)
        self.confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_edit.setPlaceholderText("再次输入密码")
        self.volume_combo = QComboBox(self)
        for label, size in VOLUME_OPTIONS:
            self.volume_combo.addItem(label, size)
        self.note_label = QLabel(self)
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet("color: #b45309;")
        target_form = QFormLayout(target_box)
        target_form.addRow("输出文件：", target_row)
        target_form.addRow("目标格式：", self.format_combo)
        target_form.addRow("压缩等级：", self.level_combo)
        target_form.addRow("设置密码：", self.password_edit)
        target_form.addRow("确认密码：", self.confirm_edit)
        target_form.addRow("分卷大小：", self.volume_combo)
        target_form.addRow("", self.note_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始转换")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(source_box)
        layout.addWidget(target_box)
        layout.addWidget(buttons)
        self._on_format_changed()

    def _default_target(self) -> Path:
        detected = ArchiveFormat.from_path(self.source) or ArchiveFormat.ZIP
        target_format = (
            ArchiveFormat.SEVEN_ZIP if detected is not ArchiveFormat.SEVEN_ZIP else ArchiveFormat.ZIP
        )
        stem = self.source.name
        for suffix in detected.suffixes:
            if stem.lower().endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        return self.source.with_name(f"{stem}{target_format.default_suffix}")

    def _choose_target(self) -> None:
        target, _ = QFileDialog.getSaveFileName(
            self,
            "选择输出文件",
            self.target_edit.text(),
            "压缩包 (*" + " *".join(ArchiveFormat.all_suffixes()) + ")",
        )
        if target:
            self.target_edit.setText(target)

    def _on_format_changed(self) -> None:
        archive_format = selected_format(self.format_combo)
        supports = archive_format.supports_password_write
        self.password_edit.setEnabled(supports)
        self.confirm_edit.setEnabled(supports)
        self.note_label.setText(archive_format.password_note)
        target = Path(self.target_edit.text().strip() or "output")
        stem = target.name
        for suffix in ArchiveFormat.all_suffixes():
            if stem.lower().endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        self.target_edit.setText(str(target.with_name(f"{stem}{archive_format.default_suffix}")))

    def options(self) -> ConvertOptions:
        return ConvertOptions(
            source=self.source,
            target=Path(self.target_edit.text().strip()),
            level=selected_level(self.level_combo),
            source_password=self.source_password_edit.text() or None,
            target_password=self.password_edit.text() or None,
            volume_size=selected_volume_size(self.volume_combo, 0),
        )

    def _on_accept(self) -> None:
        target_text = self.target_edit.text().strip()
        if not target_text:
            QMessageBox.warning(self, "缺少输出文件", "请填写输出文件路径。")
            return
        options = self.options()
        if options.target.resolve() == self.source.resolve():
            QMessageBox.warning(self, "路径冲突", "输出文件不能与源文件相同。")
            return
        if options.target.exists():
            answer = QMessageBox.question(
                self,
                "目标已存在",
                f"{options.target.name} 已存在，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if (
            self.password_edit.isEnabled()
            and self.password_edit.text()
            and self.password_edit.text() != self.confirm_edit.text()
        ):
            QMessageBox.warning(self, "密码不一致", "两次输入的密码不一致。")
            return
        if self.volume_combo.currentData() == -1:
            QMessageBox.information(self, "自定义分卷", "请选择预设分卷大小或不分卷。")
            return
        self.accept()


class BatchConvertDialog(ConvertDialog):
    """批量转换的占位实现：复用单文件转换对话框，由主窗口循环入队。"""

    def __init__(self, *args, sources: list[Path], **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.sources = [Path(item) for item in sources]
        super().__init__(*args, source=self.sources[0], **kwargs)
        self.setWindowTitle(f"批量转换（{len(self.sources)} 个压缩包）")
