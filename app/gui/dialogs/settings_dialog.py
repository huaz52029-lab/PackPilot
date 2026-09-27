"""设置对话框：主题、默认参数、安全选项与 Windows 集成。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.gui.dialogs.common import fill_format_combo, fill_level_combo, selected_format, selected_level
from app.services.settings import THEME_DARK, THEME_LIGHT, THEME_SYSTEM, SettingsService
from app.version import VERSION_DISPLAY
from app.windows import context_menu, file_association
from app.windows.shell_execute import open_directory


class SettingsDialog(QDialog):
    """集中管理用户配置与系统集成开关。"""

    theme_changed = Signal(str)
    associations_changed = Signal()

    def __init__(self, parent: QWidget | None = None, *, settings: SettingsService) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(620, 640)
        self.settings = settings

        # --- 外观 ---
        appearance = QGroupBox("外观与行为", self)
        self.theme_combo = QComboBox(self)
        self.theme_combo.addItem("跟随系统", THEME_SYSTEM)
        self.theme_combo.addItem("浅色", THEME_LIGHT)
        self.theme_combo.addItem("深色", THEME_DARK)
        index = self.theme_combo.findData(settings.settings.theme)
        self.theme_combo.setCurrentIndex(max(index, 0))
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.recent_spin = QSpinBox(self)
        self.recent_spin.setRange(1, 50)
        self.recent_spin.setValue(settings.recent_limit)
        self.task_panel_check = QCheckBox("显示任务中心面板", self)
        self.task_panel_check.setChecked(settings.settings.task_panel_visible)
        self.smart_check = QCheckBox("默认使用智能解压", self)
        self.smart_check.setChecked(settings.settings.smart_extract)
        self.confirm_delete_check = QCheckBox("删除压缩包内条目前询问", self)
        self.confirm_delete_check.setChecked(settings.settings.confirm_delete)
        appearance_form = QFormLayout(appearance)
        appearance_form.addRow("主题：", self.theme_combo)
        appearance_form.addRow("最近使用数量：", self.recent_spin)
        appearance_form.addRow("", self.task_panel_check)
        appearance_form.addRow("", self.smart_check)
        appearance_form.addRow("", self.confirm_delete_check)

        # --- 默认参数 ---
        defaults = QGroupBox("默认压缩参数", self)
        self.format_combo = QComboBox(self)
        fill_format_combo(self.format_combo, current=settings.default_format)
        self.level_combo = QComboBox(self)
        fill_level_combo(self.level_combo, current=settings.default_level)
        defaults_form = QFormLayout(defaults)
        defaults_form.addRow("默认格式：", self.format_combo)
        defaults_form.addRow("默认压缩等级：", self.level_combo)

        # --- 安全 ---
        safety = QGroupBox("安全选项", self)
        self.safety_check = QCheckBox("启用安全检查（路径穿越、解压炸弹、磁盘空间）", self)
        self.safety_check.setChecked(settings.settings.safety_enabled)
        safety_note = QLabel(
            "安全检查始终会阻止越界路径（Zip Slip）。关闭后将放宽压缩率与总大小限制，"
            "但仍不会写入目标目录之外的文件。",
            self,
        )
        safety_note.setWordWrap(True)
        safety_note.setStyleSheet("color: #6b7280;")
        safety_layout = QVBoxLayout(safety)
        safety_layout.addWidget(self.safety_check)
        safety_layout.addWidget(safety_note)

        # --- Windows 集成 ---
        integration = QGroupBox("Windows 集成（当前用户，无需管理员权限）", self)
        self.assoc_status = QLabel(file_association.association_summary(), self)
        self.menu_status = QLabel(context_menu.context_menu_summary(), self)
        install_assoc_button = QPushButton("安装文件关联", self)
        install_assoc_button.clicked.connect(self._install_associations)
        remove_assoc_button = QPushButton("移除文件关联", self)
        remove_assoc_button.clicked.connect(self._remove_associations)
        install_menu_button = QPushButton("安装右键菜单", self)
        install_menu_button.clicked.connect(self._install_menu)
        remove_menu_button = QPushButton("移除右键菜单", self)
        remove_menu_button.clicked.connect(self._remove_menu)
        assoc_row = QHBoxLayout()
        assoc_row.addWidget(install_assoc_button)
        assoc_row.addWidget(remove_assoc_button)
        assoc_row.addStretch(1)
        menu_row = QHBoxLayout()
        menu_row.addWidget(install_menu_button)
        menu_row.addWidget(remove_menu_button)
        menu_row.addStretch(1)
        self.integration_note = QLabel(
            f"命令行手动注册：{file_association.registration_hint()}\n{context_menu.explorer_restart_hint()}",
            self,
        )
        self.integration_note.setWordWrap(True)
        self.integration_note.setStyleSheet("color: #6b7280;")
        integration_layout = QVBoxLayout(integration)
        integration_layout.addWidget(self.assoc_status)
        integration_layout.addLayout(assoc_row)
        integration_layout.addWidget(self.menu_status)
        integration_layout.addLayout(menu_row)
        integration_layout.addWidget(self.integration_note)

        # --- 目录与日志 ---
        paths_box = QGroupBox("数据与日志", self)
        from app.services import paths as paths_service

        config_label = QLabel(f"配置目录：{paths_service.config_dir()}", self)
        log_label = QLabel(f"日志目录：{paths_service.logs_dir()}", self)
        open_config_button = QPushButton("打开配置目录", self)
        open_config_button.clicked.connect(lambda: open_directory(paths_service.config_dir()))
        open_log_button = QPushButton("打开日志目录", self)
        open_log_button.clicked.connect(lambda: open_directory(paths_service.logs_dir()))
        paths_row = QHBoxLayout()
        paths_row.addWidget(open_config_button)
        paths_row.addWidget(open_log_button)
        paths_row.addStretch(1)
        paths_layout = QVBoxLayout(paths_box)
        paths_layout.addWidget(config_label)
        paths_layout.addWidget(log_label)
        paths_layout.addLayout(paths_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        restore = buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults)
        restore.setText("恢复默认")
        restore.clicked.connect(self._restore_defaults)

        layout = QVBoxLayout(self)
        layout.addWidget(appearance)
        layout.addWidget(defaults)
        layout.addWidget(safety)
        layout.addWidget(integration)
        layout.addWidget(paths_box)
        layout.addWidget(QLabel(f"版本：{VERSION_DISPLAY}", self))
        layout.addWidget(buttons)

    # ------------------------------------------------------------
    def _on_theme_changed(self) -> None:
        theme = str(self.theme_combo.currentData())
        self.settings.update(theme=theme)
        self.theme_changed.emit(theme)

    def _on_accept(self) -> None:
        self.settings.update(
            theme=str(self.theme_combo.currentData()),
            recent_limit=self.recent_spin.value(),
            task_panel_visible=self.task_panel_check.isChecked(),
            smart_extract=self.smart_check.isChecked(),
            confirm_delete=self.confirm_delete_check.isChecked(),
            safety_enabled=self.safety_check.isChecked(),
            default_format=selected_format(self.format_combo).value,
            default_level=selected_level(self.level_combo).value,
        )
        self.accept()

    def _restore_defaults(self) -> None:
        answer = QMessageBox.question(
            self,
            "恢复默认设置",
            "确定要恢复全部默认设置吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.settings.reset()
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(THEME_SYSTEM))
        self.recent_spin.setValue(self.settings.recent_limit)
        self.task_panel_check.setChecked(self.settings.settings.task_panel_visible)
        self.smart_check.setChecked(self.settings.settings.smart_extract)
        self.confirm_delete_check.setChecked(self.settings.settings.confirm_delete)
        self.safety_check.setChecked(self.settings.settings.safety_enabled)
        self.theme_changed.emit(self.settings.settings.theme)

    def _install_associations(self) -> None:
        registered = file_association.install_file_associations()
        if registered:
            self.settings.update(file_association_installed=True)
            self.assoc_status.setText(file_association.association_summary())
            self.associations_changed.emit()
            QMessageBox.information(self, "文件关联", "已注册以下扩展名：\n" + "、".join(registered))
        else:
            QMessageBox.warning(self, "文件关联", "当前系统不支持写入注册表（仅支持 Windows）。")

    def _remove_associations(self) -> None:
        file_association.remove_file_associations()
        self.settings.update(file_association_installed=False)
        self.assoc_status.setText(file_association.association_summary())
        self.associations_changed.emit()

    def _install_menu(self) -> None:
        if context_menu.install_context_menu():
            self.settings.update(context_menu_installed=True)
            self.menu_status.setText(context_menu.context_menu_summary())
            self.associations_changed.emit()
            QMessageBox.information(self, "右键菜单", "已安装右键菜单。")
        else:
            QMessageBox.warning(self, "右键菜单", "当前系统不支持写入注册表（仅支持 Windows）。")

    def _remove_menu(self) -> None:
        context_menu.remove_context_menu()
        self.settings.update(context_menu_installed=False)
        self.menu_status.setText(context_menu.context_menu_summary())
        self.associations_changed.emit()
