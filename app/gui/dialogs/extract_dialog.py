"""解压 / 批量解压对话框。"""

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
    QVBoxLayout,
    QWidget,
)

from app.core.archive_info import ArchiveInfo
from app.core.archive_security import check_disk_space
from app.core.options import ExtractOptions, OverwritePolicy, SafetyLimits
from app.core.smart_extract import plan_smart_extract, suggested_extract_directory
from app.core.utils import human_size
from app.gui.dialogs.common import disk_space_text
from app.services.settings import SettingsService


class ExtractDialog(QDialog):
    """解压前展示压缩包信息、目标目录、磁盘空间与冲突策略。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        info: ArchiveInfo,
        settings: SettingsService,
        entries: list[str] | None = None,
        limits: SafetyLimits | None = None,
        encrypted: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("解压压缩包")
        self.resize(680, 620)
        self.info = info
        self.settings = settings
        self.entries = entries
        self.limits = limits or SafetyLimits()

        selected = None
        if entries:
            selected = set(entries)
        self.required_bytes = sum(
            entry.size
            for entry in info.entries
            if not entry.is_dir
            and (
                selected is None
                or entry.name in selected
                or entry.name.rstrip("/") in {item.rstrip("/") for item in entries or []}
                or any(entry.name.startswith(item.rstrip("/") + "/") for item in entries or [])
            )
        )
        self.file_count = len(entries) if entries else info.file_count

        info_box = QGroupBox("压缩包信息", self)
        info_form = QFormLayout(info_box)
        info_form.addRow("压缩包名称：", QLabel(info.path.name, self))
        info_form.addRow("压缩格式：", QLabel(info.format.display_name, self))
        info_form.addRow(
            "条目数量：",
            QLabel(
                f"{self.file_count} 个（全部 {info.file_count} 个文件 / {info.dir_count} 个目录）"
                if entries
                else f"{info.file_count} 个文件 / {info.dir_count} 个目录",
                self,
            ),
        )
        info_form.addRow("压缩大小：", QLabel(human_size(info.total_compressed_size), self))
        info_form.addRow("预计解压大小：", QLabel(human_size(self.required_bytes), self))
        if info.is_volume_set:
            info_form.addRow("分卷：", QLabel(f"{len(info.volumes)} 卷", self))
        if info.encrypted or encrypted:
            info_form.addRow("加密：", QLabel("已加密，需要密码", self))

        target_box = QGroupBox("解压目标", self)
        self.target_edit = QLineEdit(self)
        browse_button = QPushButton("浏览…", self)
        browse_button.clicked.connect(self._choose_target)
        target_row = QHBoxLayout()
        target_row.addWidget(self.target_edit, 1)
        target_row.addWidget(browse_button)
        self.smart_check = QCheckBox("智能解压（自动避免重复嵌套目录）", self)
        self.smart_check.setChecked(settings.settings.smart_extract)
        self.smart_check.toggled.connect(self._on_smart_changed)
        self.smart_hint = QLabel(self)
        self.smart_hint.setWordWrap(True)
        self.smart_hint.setStyleSheet("color: #6b7280;")
        self.space_label = QLabel(self)
        self.space_label.setWordWrap(True)
        target_form = QFormLayout(target_box)
        target_form.addRow("目标目录：", target_row)
        target_form.addRow("", self.smart_check)
        target_form.addRow("", self.smart_hint)
        target_form.addRow("磁盘空间：", self.space_label)
        self.target_edit.textChanged.connect(self._update_space)

        options_box = QGroupBox("选项", self)
        self.policy_combo = QComboBox(self)
        for policy in OverwritePolicy:
            self.policy_combo.addItem(policy.label, policy.value)
        rename_index = self.policy_combo.findData(OverwritePolicy.RENAME.value)
        self.policy_combo.setCurrentIndex(max(rename_index, 0))
        self.password_edit = QLineEdit(self)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("压缩包已加密时必填")
        self.password_edit.setEnabled(info.encrypted or encrypted)
        options_form = QFormLayout(options_box)
        options_form.addRow("同名文件：", self.policy_combo)
        options_form.addRow("密码：", self.password_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始解压")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(info_box)
        layout.addWidget(target_box)
        layout.addWidget(options_box)
        layout.addWidget(buttons)

        self.target_edit.setText(
            str(suggested_extract_directory(info.path, info, smart=self.smart_check.isChecked()))
        )
        self._on_smart_changed(self.smart_check.isChecked())
        self._update_space()

    # ------------------------------------------------------------
    def _choose_target(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择解压目录", self.target_edit.text())
        if folder:
            self.target_edit.setText(folder)

    def _on_smart_changed(self, enabled: bool) -> None:
        plan = plan_smart_extract(self.info.path, self.info)
        self.smart_hint.setText(("智能解压：" if enabled else "普通解压：") + plan.reason)
        self.target_edit.setText(str(suggested_extract_directory(self.info.path, self.info, smart=enabled)))
        self._update_space()

    def _update_space(self) -> None:
        target = Path(self.target_edit.text().strip() or Path.cwd())
        report = check_disk_space(target, self.required_bytes)
        self.space_label.setText(disk_space_text(report))
        self.space_label.setStyleSheet("color: #15803d;" if report.sufficient else "color: #b91c1c;")

    def options(self) -> ExtractOptions:
        policy = OverwritePolicy(str(self.policy_combo.currentData()))
        return ExtractOptions(
            target=Path(self.target_edit.text().strip()),
            entries=self.entries,
            password=self.password_edit.text() or None,
            policy=policy,
            smart=self.smart_check.isChecked(),
            limits=self.limits,
            check_space=True,
        )

    def _on_accept(self) -> None:
        target = self.target_edit.text().strip()
        if not target:
            QMessageBox.warning(self, "缺少目标目录", "请选择解压目标目录。")
            return
        if (self.info.encrypted or self.password_edit.isEnabled()) and not self.password_edit.text():
            answer = QMessageBox.question(
                self,
                "未填写密码",
                "该压缩包已加密但未填写密码，继续可能会失败。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        report = check_disk_space(Path(target), self.required_bytes)
        if not report.sufficient:
            QMessageBox.critical(
                self,
                "磁盘空间不足",
                "⚠ 磁盘空间不足，已阻止开始任务。\n\n" + disk_space_text(report),
            )
            return
        self.settings.remember_extract_directory(Path(target))
        self.accept()


class BatchExtractDialog(QDialog):
    """批量解压：多个压缩包统一加入任务队列。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        settings: SettingsService,
        archives: list[Path] | None = None,
        limits: SafetyLimits | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("批量解压")
        self.resize(680, 520)
        self.settings = settings
        self.limits = limits or SafetyLimits()

        list_box = QGroupBox("待解压的压缩包", self)
        self.archive_list = QListWidget(self)
        self.archive_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        add_button = QPushButton("添加压缩包…", self)
        add_button.clicked.connect(self._add_archives)
        remove_button = QPushButton("移除选中", self)
        remove_button.clicked.connect(self._remove_selected)
        clear_button = QPushButton("清空", self)
        clear_button.clicked.connect(self.archive_list.clear)
        row = QHBoxLayout()
        for button in (add_button, remove_button, clear_button):
            row.addWidget(button)
        row.addStretch(1)
        list_layout = QVBoxLayout(list_box)
        list_layout.addWidget(self.archive_list, 1)
        list_layout.addLayout(row)

        target_box = QGroupBox("解压设置", self)
        self.target_edit = QLineEdit(self)
        browse_button = QPushButton("浏览…", self)
        browse_button.clicked.connect(self._choose_target)
        target_row = QHBoxLayout()
        target_row.addWidget(self.target_edit, 1)
        target_row.addWidget(browse_button)
        self.subdir_check = QCheckBox("每个压缩包解压到独立子目录（推荐）", self)
        self.subdir_check.setChecked(True)
        self.policy_combo = QComboBox(self)
        for policy in OverwritePolicy:
            self.policy_combo.addItem(policy.label, policy.value)
        rename_index = self.policy_combo.findData(OverwritePolicy.RENAME.value)
        self.policy_combo.setCurrentIndex(max(rename_index, 0))
        self.password_edit = QLineEdit(self)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("如压缩包需要密码")
        target_form = QFormLayout(target_box)
        target_form.addRow("目标目录：", target_row)
        target_form.addRow("", self.subdir_check)
        target_form.addRow("同名文件：", self.policy_combo)
        target_form.addRow("密码：", self.password_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("加入任务队列")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(list_box, 2)
        layout.addWidget(target_box)
        layout.addWidget(buttons)

        self.target_edit.setText(str(settings.last_extract_directory or Path.home() / "Documents"))
        for archive in archives or []:
            self.archive_list.addItem(str(archive))

    def _add_archives(self) -> None:
        from app.core.archive_info import ArchiveFormat

        patterns = " ".join(f"*{suffix}" for suffix in ArchiveFormat.all_suffixes())
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择压缩包",
            str(self.settings.last_extract_directory or Path.home()),
            f"压缩包 ({patterns})",
        )
        existing = {self.archive_list.item(index).text() for index in range(self.archive_list.count())}
        for file_name in files:
            if file_name not in existing:
                self.archive_list.addItem(file_name)

    def _remove_selected(self) -> None:
        for item in self.archive_list.selectedItems():
            self.archive_list.takeItem(self.archive_list.row(item))

    def _choose_target(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择目标目录", self.target_edit.text())
        if folder:
            self.target_edit.setText(folder)

    def archives(self) -> list[Path]:
        return [Path(self.archive_list.item(index).text()) for index in range(self.archive_list.count())]

    def target_dir(self) -> Path:
        return Path(self.target_edit.text().strip())

    def options_template(self) -> ExtractOptions:
        return ExtractOptions(
            target=self.target_dir(),
            password=self.password_edit.text() or None,
            policy=OverwritePolicy(str(self.policy_combo.currentData())),
            limits=self.limits,
        )

    def _on_accept(self) -> None:
        if not self.archives():
            QMessageBox.warning(self, "缺少压缩包", "请至少添加一个压缩包。")
            return
        target = self.target_dir()
        if not target:
            QMessageBox.warning(self, "缺少目标目录", "请选择目标目录。")
            return
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "无法创建目录", f"{target}\n{exc}")
            return
        self.settings.remember_extract_directory(target)
        self.accept()
