"""新建压缩包 / 批量压缩对话框。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.archive_info import ArchiveFormat
from app.core.options import CreateOptions
from app.core.utils import count_tree, human_size, sanitize_filename
from app.gui.dialogs.common import (
    VOLUME_OPTIONS,
    fill_format_combo,
    fill_level_combo,
    selected_format,
    selected_level,
    selected_volume_size,
)
from app.gui.widgets.password_strength import PasswordStrengthWidget
from app.services.settings import SettingsService


class NewArchiveDialog(QDialog):
    """收集创建压缩包所需参数（文件、格式、等级、密码、分卷）。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        settings: SettingsService,
        initial_sources: list[Path] | None = None,
        initial_target: Path | None = None,
        title: str = "新建压缩包",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 640)
        self.settings = settings

        # --- 来源列表 ---
        sources_box = QGroupBox("要压缩的文件和文件夹", self)
        self.source_list = QListWidget(self)
        self.source_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        add_files_button = QPushButton("添加文件…", self)
        add_files_button.clicked.connect(self._add_files)
        add_folder_button = QPushButton("添加文件夹…", self)
        add_folder_button.clicked.connect(self._add_folder)
        remove_button = QPushButton("移除选中", self)
        remove_button.clicked.connect(self._remove_selected)
        clear_button = QPushButton("清空", self)
        clear_button.clicked.connect(self.source_list.clear)
        source_buttons = QHBoxLayout()
        for button in (add_files_button, add_folder_button, remove_button, clear_button):
            source_buttons.addWidget(button)
        source_buttons.addStretch(1)
        self.source_summary = QLabel("尚未添加任何文件", self)
        self.source_summary.setStyleSheet("color: #6b7280;")
        sources_layout = QVBoxLayout(sources_box)
        sources_layout.addWidget(self.source_list, 1)
        sources_layout.addLayout(source_buttons)
        sources_layout.addWidget(self.source_summary)

        # --- 输出与格式 ---
        output_box = QGroupBox("输出设置", self)
        self.output_dir_edit = QLineEdit(self)
        browse_dir_button = QPushButton("浏览…", self)
        browse_dir_button.clicked.connect(self._choose_output_dir)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.output_dir_edit, 1)
        dir_row.addWidget(browse_dir_button)

        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText("例如 project")
        self.format_combo = QComboBox(self)
        fill_format_combo(self.format_combo, current=settings.default_format)
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        self.level_combo = QComboBox(self)
        fill_level_combo(self.level_combo, current=settings.default_level)
        self.level_hint = QLabel(self)
        self.level_hint.setStyleSheet("color: #6b7280;")
        self.level_combo.currentIndexChanged.connect(self._update_level_hint)

        form = QFormLayout(output_box)
        form.addRow("输出目录：", dir_row)
        form.addRow("文件名：", self.name_edit)
        form.addRow("压缩格式：", self.format_combo)
        form.addRow("压缩等级：", self.level_combo)
        form.addRow("", self.level_hint)

        # --- 密码 ---
        password_box = QGroupBox("密码保护", self)
        self.password_check = QCheckBox("启用密码保护", self)
        self.password_check.toggled.connect(self._on_password_toggled)
        self.password_edit = QLineEdit(self)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("密码")
        self.confirm_edit = QLineEdit(self)
        self.confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_edit.setPlaceholderText("再次输入密码")
        self.strength_widget = PasswordStrengthWidget(self)
        self.password_edit.textChanged.connect(self.strength_widget.set_password)
        self.password_note = QLabel(self)
        self.password_note.setWordWrap(True)
        self.password_note.setStyleSheet("color: #b45309;")
        password_form = QFormLayout(password_box)
        password_form.addRow(self.password_check)
        password_form.addRow("设置密码：", self.password_edit)
        password_form.addRow("确认密码：", self.confirm_edit)
        password_form.addRow("", self.strength_widget)
        password_form.addRow("", self.password_note)

        # --- 分卷 ---
        volume_box = QGroupBox("分卷压缩", self)
        self.volume_combo = QComboBox(self)
        for label, size in VOLUME_OPTIONS:
            self.volume_combo.addItem(label, size)
        self.volume_combo.currentIndexChanged.connect(self._on_volume_changed)
        self.custom_volume_spin = QSpinBox(self)
        self.custom_volume_spin.setRange(1, 100_000)
        self.custom_volume_spin.setSuffix(" MB")
        self.custom_volume_spin.setValue(200)
        self.custom_volume_spin.setEnabled(False)
        self.volume_note = QLabel(
            "分卷将生成 project.zip.001、project.zip.002 … 并附带清单文件用于完整性校验。",
            self,
        )
        self.volume_note.setStyleSheet("color: #6b7280;")
        self.volume_note.setWordWrap(True)
        volume_form = QFormLayout(volume_box)
        volume_form.addRow("分卷大小：", self.volume_combo)
        volume_form.addRow("自定义大小：", self.custom_volume_spin)
        volume_form.addRow("", self.volume_note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始压缩")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        self._buttons = buttons

        layout = QVBoxLayout(self)
        layout.addWidget(sources_box, 2)
        layout.addWidget(output_box)
        layout.addWidget(password_box)
        layout.addWidget(volume_box)
        layout.addWidget(buttons)

        for source in initial_sources or []:
            self._append_source(Path(source))
        self.output_dir_edit.setText(
            str(initial_target.parent) if initial_target else str(self._default_dir())
        )
        if initial_target is not None and initial_target.stem:
            self.name_edit.setText(initial_target.stem)
        elif initial_sources:
            first = Path(initial_sources[0])
            self.name_edit.setText(sanitize_filename(first.stem if first.is_file() else first.name))
        self._update_level_hint()
        self._on_format_changed()
        self._update_summary()

    # ------------------------------------------------------------ 来源
    def _append_source(self, path: Path) -> None:
        existing = {self.source_list.item(index).text() for index in range(self.source_list.count())}
        if str(path) in existing:
            return
        self.source_list.addItem(str(path))
        self._update_summary()

    def _add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "选择要压缩的文件", str(self._default_dir()))
        for file_name in files:
            self._append_source(Path(file_name))

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择要压缩的文件夹", str(self._default_dir()))
        if folder:
            self._append_source(Path(folder))

    def _remove_selected(self) -> None:
        for item in self.source_list.selectedItems():
            self.source_list.takeItem(self.source_list.row(item))
        self._update_summary()

    def sources(self) -> list[Path]:
        return [Path(self.source_list.item(index).text()) for index in range(self.source_list.count())]

    def _update_summary(self) -> None:
        files = 0
        total = 0
        for source in self.sources():
            count, size = count_tree(source)
            files += count
            total += size
        if not files and not self.sources():
            self.source_summary.setText("尚未添加任何文件")
            return
        self.source_summary.setText(f"共 {files} 个文件，合计 {human_size(total)}")

    # ------------------------------------------------------------ 输出
    def _default_dir(self) -> Path:
        remembered = self.settings.last_directory
        if remembered is not None and remembered.exists():
            return remembered
        sources = self.sources()
        if sources:
            return sources[0].parent
        return Path.home() / "Documents"

    def _choose_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_dir_edit.text())
        if folder:
            self.output_dir_edit.setText(folder)

    def _on_format_changed(self) -> None:
        archive_format = selected_format(self.format_combo)
        supports_password = archive_format.supports_password_write
        self.password_check.setEnabled(supports_password)
        if not supports_password:
            self.password_check.setChecked(False)
            self.password_edit.setEnabled(False)
            self.confirm_edit.setEnabled(False)
            self.password_note.setText(archive_format.password_note)
        else:
            self.password_note.setText(archive_format.password_note)
        suffix = archive_format.default_suffix
        name = self.name_edit.text().strip()
        if name:
            for other in ArchiveFormat:
                for candidate in other.suffixes:
                    if name.lower().endswith(candidate):
                        name = name[: -len(candidate)]
                        break
            self.name_edit.setText(name.strip())
        self.volume_note.setText(
            f"分卷将生成 {name or 'project'}{suffix}.001、.002 … 并附带清单文件用于完整性校验。"
        )

    def _on_password_toggled(self, enabled: bool) -> None:
        self.password_edit.setEnabled(enabled)
        self.confirm_edit.setEnabled(enabled)
        self.strength_widget.setVisible(enabled)
        if enabled and not self.password_note.text():
            self.password_note.setText(selected_format(self.format_combo).password_note)

    def _update_level_hint(self) -> None:
        level = selected_level(self.level_combo)
        self.level_hint.setText(level.description)

    def _on_volume_changed(self) -> None:
        self.custom_volume_spin.setEnabled(self.volume_combo.currentData() == -1)

    # ------------------------------------------------------------ 结果
    def target_path(self) -> Path:
        archive_format = selected_format(self.format_combo)
        name = sanitize_filename(self.name_edit.text().strip() or "archive")
        for suffix in ArchiveFormat.all_suffixes():
            if name.lower().endswith(suffix):
                name = name[: -len(suffix)]
                break
        return Path(self.output_dir_edit.text().strip()) / f"{name}{archive_format.default_suffix}"

    def create_options(self) -> CreateOptions:
        archive_format = selected_format(self.format_combo)
        password = self.password_edit.text() if self.password_check.isChecked() else None
        return CreateOptions(
            target=self.target_path(),
            sources=self.sources(),
            format=archive_format,
            level=selected_level(self.level_combo),
            password=password,
            volume_size=selected_volume_size(
                self.volume_combo, self.custom_volume_spin.value() * 1024 * 1024
            ),
            replace_existing=True,
        )

    def _on_accept(self) -> None:
        sources = self.sources()
        if not sources:
            QMessageBox.warning(self, "缺少内容", "请先添加要压缩的文件或文件夹。")
            return
        output_dir = Path(self.output_dir_edit.text().strip())
        if not output_dir:
            QMessageBox.warning(self, "缺少输出目录", "请选择输出目录。")
            return
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "无法创建输出目录", f"{output_dir}\n{exc}")
            return
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "缺少文件名", "请输入压缩包文件名。")
            return
        archive_format = selected_format(self.format_combo)
        if self.password_check.isChecked():
            if not archive_format.supports_password_write:
                QMessageBox.warning(self, "不支持密码", archive_format.password_note)
                return
            if not self.password_edit.text():
                QMessageBox.warning(self, "缺少密码", "请输入密码。")
                return
            if self.password_edit.text() != self.confirm_edit.text():
                QMessageBox.warning(self, "密码不一致", "两次输入的密码不一致。")
                return
        target = self.target_path()
        if target.exists():
            answer = QMessageBox.question(
                self,
                "文件已存在",
                f"{target.name} 已存在，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.accept()


class BatchCompressDialog(QDialog):
    """批量压缩：每个文件夹独立压缩，或全部合并为一个压缩包。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        settings: SettingsService,
        folders: list[Path] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("批量压缩")
        self.resize(680, 560)
        self.settings = settings

        list_box = QGroupBox("待压缩项目", self)
        self.folder_list = QListWidget(self)
        self.folder_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        add_button = QPushButton("添加文件夹…", self)
        add_button.clicked.connect(self._add_folders)
        remove_button = QPushButton("移除选中", self)
        remove_button.clicked.connect(self._remove_selected)
        clear_button = QPushButton("清空", self)
        clear_button.clicked.connect(self.folder_list.clear)
        row = QHBoxLayout()
        for button in (add_button, remove_button, clear_button):
            row.addWidget(button)
        row.addStretch(1)
        list_layout = QVBoxLayout(list_box)
        list_layout.addWidget(self.folder_list, 1)
        list_layout.addLayout(row)

        mode_box = QGroupBox("压缩方式", self)
        self.per_folder_radio = QRadioButton("每个文件夹生成一个压缩包（项目A → 项目A.zip）", self)
        self.per_folder_radio.setChecked(True)
        self.merge_radio = QRadioButton("全部压缩成一个压缩包", self)
        mode_layout = QVBoxLayout(mode_box)
        mode_layout.addWidget(self.per_folder_radio)
        mode_layout.addWidget(self.merge_radio)
        self.merge_name_edit = QLineEdit("合并压缩包", self)
        self.merge_name_edit.setEnabled(False)
        self.merge_radio.toggled.connect(self.merge_name_edit.setEnabled)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("合并后文件名：", self))
        name_row.addWidget(self.merge_name_edit, 1)
        mode_layout.addLayout(name_row)

        output_box = QGroupBox("输出设置", self)
        self.output_dir_edit = QLineEdit(self)
        browse_button = QPushButton("浏览…", self)
        browse_button.clicked.connect(self._choose_dir)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.output_dir_edit, 1)
        dir_row.addWidget(browse_button)
        self.format_combo = QComboBox(self)
        fill_format_combo(self.format_combo, current=settings.default_format)
        self.level_combo = QComboBox(self)
        fill_level_combo(self.level_combo, current=settings.default_level)
        self.volume_combo = QComboBox(self)
        for label, size in VOLUME_OPTIONS:
            self.volume_combo.addItem(label, size)
        output_form = QFormLayout(output_box)
        output_form.addRow("输出目录：", dir_row)
        output_form.addRow("压缩格式：", self.format_combo)
        output_form.addRow("压缩等级：", self.level_combo)
        output_form.addRow("分卷大小：", self.volume_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始批量压缩")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(list_box, 2)
        layout.addWidget(mode_box)
        layout.addWidget(output_box)
        layout.addWidget(buttons)

        for folder in folders or []:
            self.folder_list.addItem(str(folder))
        self.output_dir_edit.setText(str(self._default_dir()))

    def _default_dir(self) -> Path:
        remembered = self.settings.last_directory
        if remembered is not None and remembered.exists():
            return remembered
        return Path.home() / "Documents"

    def _add_folders(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹", str(self._default_dir()))
        if folder:
            self.folder_list.addItem(folder)

    def _remove_selected(self) -> None:
        for item in self.folder_list.selectedItems():
            self.folder_list.takeItem(self.folder_list.row(item))

    def _choose_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_dir_edit.text())
        if folder:
            self.output_dir_edit.setText(folder)

    def folders(self) -> list[Path]:
        return [Path(self.folder_list.item(index).text()) for index in range(self.folder_list.count())]

    def merge_mode(self) -> bool:
        return self.merge_radio.isChecked()

    def output_dir(self) -> Path:
        return Path(self.output_dir_edit.text().strip())

    def template(self) -> CreateOptions:
        archive_format = selected_format(self.format_combo)
        name = sanitize_filename(self.merge_name_edit.text().strip() or "合并压缩包")
        return CreateOptions(
            target=self.output_dir() / f"{name}{archive_format.default_suffix}",
            sources=self.folders(),
            format=archive_format,
            level=selected_level(self.level_combo),
            volume_size=selected_volume_size(self.volume_combo, 0),
            include_root=True,
        )

    def _on_accept(self) -> None:
        if not self.folders():
            QMessageBox.warning(self, "缺少项目", "请至少添加一个文件夹。")
            return
        output_dir = self.output_dir()
        if not output_dir:
            QMessageBox.warning(self, "缺少输出目录", "请选择输出目录。")
            return
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "无法创建目录", f"{output_dir}\n{exc}")
            return
        if self.volume_combo.currentData() == -1:
            QMessageBox.information(
                self, "自定义分卷", "批量压缩暂不支持自定义分卷大小，请选择预设大小或不分卷。"
            )
            return
        self.accept()
