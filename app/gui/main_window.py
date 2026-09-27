"""PackPilot 主窗口：菜单、工具栏、地址栏、压缩包浏览与任务中心。"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QStyle,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.core.archive_info import ArchiveFormat
from app.core.archive_manager import ArchiveManager
from app.core.archive_security import IssueLevel, SecurityIssue, describe_issues
from app.core.errors import PackPilotError, PasswordRequiredError, UnsafeArchiveError
from app.core.options import (
    CreateOptions,
    ExtractOptions,
    OverwritePolicy,
    SafetyLimits,
)
from app.core.utils import human_size, is_archive_name
from app.gui.archive_view import ArchiveView
from app.gui.dialogs.about_dialog import AboutDialog
from app.gui.dialogs.convert_dialog import ConvertDialog
from app.gui.dialogs.details_dialog import DetailsDialog
from app.gui.dialogs.extract_dialog import BatchExtractDialog, ExtractDialog
from app.gui.dialogs.hash_dialog import HashDialog
from app.gui.dialogs.history_dialog import HistoryDialog
from app.gui.dialogs.new_archive_dialog import BatchCompressDialog, NewArchiveDialog
from app.gui.dialogs.password_dialog import PasswordDialog
from app.gui.dialogs.settings_dialog import SettingsDialog
from app.gui.task_panel import TaskPanel
from app.gui.theme import apply_theme
from app.gui.widgets.home_page import HomePage
from app.services.history import HistoryService
from app.services.logger import setup_logging
from app.services.recent_files import RecentFilesService
from app.services.settings import SettingsService
from app.services.temp_manager import TempManager
from app.tasks.base_task import TaskInfo, TaskStatus
from app.tasks.compress_task import BatchCompressTask, CompressTask, MergeCompressTask
from app.tasks.convert_task import ConvertTask
from app.tasks.extract_task import BatchExtractTask, ExtractTask, OpenEntryTask
from app.tasks.scan_task import ScanTask
from app.tasks.task_manager import TaskManager
from app.tasks.test_task import TestTask
from app.version import VERSION_DISPLAY
from app.windows import context_menu, file_association
from app.windows.shell_execute import open_directory, open_with_default_program

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """PackPilot 主窗口。"""

    archive_opened = Signal(Path)

    def __init__(
        self,
        *,
        settings: SettingsService | None = None,
        recent: RecentFilesService | None = None,
        history: HistoryService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings or SettingsService()
        self.recent = recent or RecentFilesService(limit=self.settings.recent_limit)
        self.history = history or HistoryService(limit=self.settings.settings.history_limit)
        self.temp_manager = TempManager(parent=self)
        self.manager = ArchiveManager(limits=self._safety_limits())
        self.task_manager = TaskManager(history=self.history, parent=self)
        self.current_archive: Path | None = None
        self._current_info = None
        self._current_password: str | None = None
        self._pending_open: dict[str, tuple[Path, Path]] = {}
        self._password_cache: dict[str, str] = {}

        self.setWindowTitle(f"{VERSION_DISPLAY} — 压缩包管理器")
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
        self.setAcceptDrops(True)
        self.resize(1180, 760)

        self.home_page = HomePage(self.recent, self)
        self.home_page.open_requested.connect(self._on_home_open_requested)
        self.home_page.new_requested.connect(self.new_archive)
        self.home_page.extract_requested.connect(lambda: self.open_archive_dialog(for_extract=True))
        self.home_page.batch_compress_requested.connect(self.batch_compress)
        self.home_page.batch_extract_requested.connect(self.batch_extract)
        self.home_page.hash_requested.connect(self.show_hash_dialog)
        self.home_page.clear_recent_requested.connect(self._clear_recent)

        self.archive_view = ArchiveView(self)
        self.archive_view.open_entry_requested.connect(self.open_entry)
        self.archive_view.extract_selected_requested.connect(self.extract_entries)
        self.archive_view.extract_all_requested.connect(lambda: self.extract_entries(None))
        self.archive_view.delete_requested.connect(self.delete_entries)
        self.archive_view.copy_path_requested.connect(self.copy_entry_paths)
        self.archive_view.properties_requested.connect(self.show_entry_properties)
        self.archive_view.refresh_requested.connect(self.refresh_archive)
        self.archive_view.add_files_requested.connect(self.add_files_to_archive)
        self.archive_view.test_requested.connect(self.test_archive)
        self.archive_view.security_requested.connect(self.scan_archive)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.archive_view)

        central = QWidget(self)
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self._build_address_bar())
        central_layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_status_bar()
        self._build_task_dock()
        self._connect_tasks()
        self._apply_saved_geometry()
        self._update_actions()
        self._refresh_task_count()

        QTimer.singleShot(0, self._startup_cleanup)

    # ------------------------------------------------------------ 构建界面
    def _safety_limits(self) -> SafetyLimits:
        if self.settings.settings.safety_enabled:
            return SafetyLimits()
        return SafetyLimits(
            warn_compression_ratio=1e12,
            block_compression_ratio=1e12,
            warn_total_size=10**15,
            block_total_size=10**15,
        )

    def _build_address_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("addressBar")
        self.back_button = QToolButton(bar)
        self.back_button.setText("←")
        self.back_button.setToolTip("后退（Alt+←）")
        self.up_button = QToolButton(bar)
        self.up_button.setText("↑")
        self.up_button.setToolTip("返回主页")
        self.address_edit = QLineEdit(bar)
        self.address_edit.setPlaceholderText("压缩包路径（输入后按回车打开）")
        self.address_edit.returnPressed.connect(self._on_address_entered)
        self.breadcrumb_label = QLabel("主页", bar)
        self.breadcrumb_label.setStyleSheet("color: #6b7280;")
        self.refresh_button = QToolButton(bar)
        self.refresh_button.setText("刷新")
        self.refresh_button.clicked.connect(self.refresh_archive)
        self.theme_combo = QComboBox(bar)
        self.theme_combo.addItem("跟随系统", "system")
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("深色", "dark")
        index = self.theme_combo.findData(self.settings.settings.theme)
        self.theme_combo.setCurrentIndex(max(index, 0))
        self.theme_combo.currentIndexChanged.connect(self._on_theme_selected)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        layout.addWidget(self.back_button)
        layout.addWidget(self.up_button)
        layout.addWidget(self.address_edit, 1)
        layout.addWidget(self.breadcrumb_label)
        layout.addWidget(self.refresh_button)
        layout.addWidget(QLabel("主题：", bar))
        layout.addWidget(self.theme_combo)
        return bar

    def _build_actions(self) -> None:
        def action(text: str, shortcut: str | None = None, *, tip: str = "") -> QAction:
            item = QAction(text, self)
            if shortcut:
                item.setShortcut(QKeySequence(shortcut))
            item.setStatusTip(tip or text)
            return item

        self.action_new = action("新建压缩包", "Ctrl+N", tip="创建新的压缩包")
        self.action_new.triggered.connect(self.new_archive)
        self.action_open = action("打开压缩包", "Ctrl+O", tip="打开压缩包")
        self.action_open.triggered.connect(lambda: self.open_archive_dialog())
        self.action_extract = action("解压", "Ctrl+E", tip="解压当前压缩包")
        self.action_extract.triggered.connect(lambda: self.extract_entries(None))
        self.action_extract_here = action("解压到当前文件夹", None, tip="智能解压到压缩包所在目录")
        self.action_extract_here.triggered.connect(self.extract_to_current_folder)
        self.action_add = action("添加文件", "Ctrl+A", tip="向当前压缩包添加文件")
        self.action_add.triggered.connect(self.add_files_to_archive)
        self.action_delete = action("删除条目", "Delete", tip="删除选中的压缩包条目")
        self.action_delete.triggered.connect(lambda: self.delete_entries(self.archive_view.selected_paths()))
        self.action_test = action("测试压缩包", "Ctrl+T", tip="完整读取并校验压缩包")
        self.action_test.triggered.connect(self.test_archive)
        self.action_scan = action("安全检查", None, tip="检查路径安全与分卷完整性")
        self.action_scan.triggered.connect(self.scan_archive)
        self.action_refresh = action("刷新", "F5", tip="刷新当前压缩包")
        self.action_refresh.triggered.connect(self.refresh_archive)
        self.action_search = action("搜索", "Ctrl+F", tip="在压缩包内搜索")
        self.action_search.triggered.connect(self.archive_view.focus_search)
        self.action_close_archive = action("关闭压缩包", "Ctrl+W", tip="返回主页")
        self.action_close_archive.triggered.connect(self.close_archive)
        self.action_convert = action("转换格式…", None, tip="ZIP ↔ 7Z ↔ TAR 格式转换")
        self.action_convert.triggered.connect(self.convert_archive)
        self.action_batch_compress = action("批量压缩…", None, tip="批量压缩多个文件夹")
        self.action_batch_compress.triggered.connect(self.batch_compress)
        self.action_batch_extract = action("批量解压…", None, tip="批量解压多个压缩包")
        self.action_batch_extract.triggered.connect(self.batch_extract)
        self.action_hash = action("计算哈希…", "Ctrl+H", tip="计算文件或压缩包的哈希")
        self.action_hash.triggered.connect(self.show_hash_dialog)
        self.action_history = action("任务历史…", None, tip="查看任务历史记录")
        self.action_history.triggered.connect(self.show_history)
        self.action_settings = action("设置…", "Ctrl+,", tip="打开设置")
        self.action_settings.triggered.connect(self.show_settings)
        self.action_about = action("关于 PackPilot", None)
        self.action_about.triggered.connect(self.show_about)
        self.action_cancel_task = action("取消当前任务", "Esc", tip="取消最近的任务")
        self.action_cancel_task.triggered.connect(self.task_manager.cancel_latest)
        self.action_exit = action("退出", "Ctrl+Q")
        self.action_exit.triggered.connect(self.close)
        self.action_install_associations = action("安装文件关联", None)
        self.action_install_associations.triggered.connect(self._install_associations)
        self.action_remove_associations = action("移除文件关联", None)
        self.action_remove_associations.triggered.connect(self._remove_associations)
        self.action_install_menu = action("安装右键菜单", None)
        self.action_install_menu.triggered.connect(self._install_menu)
        self.action_remove_menu = action("移除右键菜单", None)
        self.action_remove_menu.triggered.connect(self._remove_menu)
        self.action_association_status = action("检查关联状态", None)
        self.action_association_status.triggered.connect(self._show_integration_status)
        self.action_open_config = action("打开配置目录", None)
        self.action_open_config.triggered.connect(lambda: open_directory(self.settings.path.parent))
        self.action_open_logs = action("打开日志目录", None)
        from app.services import paths as paths_service

        self.action_open_logs.triggered.connect(lambda: open_directory(paths_service.logs_dir()))
        self.action_shortcuts = action("快捷键说明", None)
        self.action_shortcuts.triggered.connect(self._show_shortcuts)

    def _build_menus(self) -> None:
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("文件(&F)")
        file_menu.addAction(self.action_new)
        file_menu.addAction(self.action_open)
        self.recent_menu = file_menu.addMenu("最近打开")
        file_menu.addSeparator()
        file_menu.addAction(self.action_extract)
        file_menu.addAction(self.action_extract_here)
        file_menu.addAction(self.action_test)
        file_menu.addAction(self.action_convert)
        file_menu.addSeparator()
        file_menu.addAction(self.action_batch_compress)
        file_menu.addAction(self.action_batch_extract)
        file_menu.addSeparator()
        file_menu.addAction(self.action_close_archive)
        file_menu.addAction(self.action_exit)

        edit_menu = menu_bar.addMenu("编辑(&E)")
        edit_menu.addAction(self.action_add)
        edit_menu.addAction(self.action_delete)
        edit_menu.addAction(self.action_search)
        edit_menu.addAction(self.action_refresh)

        tools_menu = menu_bar.addMenu("工具(&T)")
        tools_menu.addAction(self.action_hash)
        tools_menu.addAction(self.action_history)
        tools_menu.addAction(self.action_scan)
        integration_menu = tools_menu.addMenu("Windows 集成")
        integration_menu.addAction(self.action_install_associations)
        integration_menu.addAction(self.action_remove_associations)
        integration_menu.addSeparator()
        integration_menu.addAction(self.action_install_menu)
        integration_menu.addAction(self.action_remove_menu)
        integration_menu.addSeparator()
        integration_menu.addAction(self.action_association_status)
        tools_menu.addSeparator()
        tools_menu.addAction(self.action_settings)

        view_menu = menu_bar.addMenu("视图(&V)")
        view_menu.addAction(self.action_refresh)
        self.task_dock_action = QAction("任务中心", self, checkable=True)
        self.task_dock_action.setChecked(self.settings.settings.task_panel_visible)
        view_menu.addAction(self.task_dock_action)
        view_menu.addSeparator()
        view_menu.addAction(self.action_open_config)
        view_menu.addAction(self.action_open_logs)

        help_menu = menu_bar.addMenu("帮助(&H)")
        help_menu.addAction(self.action_shortcuts)
        help_menu.addAction(self.action_about)
        self._refresh_recent_menu()

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("主工具栏", self)
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        toolbar.addAction(self.action_new)
        toolbar.addAction(self.action_open)
        toolbar.addSeparator()
        toolbar.addAction(self.action_add)
        toolbar.addAction(self.action_extract)
        toolbar.addAction(self.action_test)
        toolbar.addAction(self.action_delete)
        toolbar.addSeparator()
        toolbar.addAction(self.action_refresh)
        search_box = QLineEdit(toolbar)
        search_box.setPlaceholderText("搜索条目（Ctrl+F）")
        search_box.setMaximumWidth(240)
        search_box.setClearButtonEnabled(True)
        search_box.textChanged.connect(self.archive_view.search_edit.setText)
        search_box.returnPressed.connect(self.archive_view.focus_search)
        toolbar.addWidget(search_box)
        self.addToolBar(toolbar)
        self.main_toolbar = toolbar

    def _build_status_bar(self) -> None:
        status = QStatusBar(self)
        self.setStatusBar(status)
        self.status_label = QLabel("就绪", self)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setVisible(False)
        self.task_count_label = QLabel("任务：0", self)
        self.cancel_button = QPushButton("取消任务", self)
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.task_manager.cancel_latest)
        status.addWidget(self.status_label, 1)
        status.addPermanentWidget(self.task_count_label)
        status.addPermanentWidget(self.progress_bar)
        status.addPermanentWidget(self.cancel_button)

    def _build_task_dock(self) -> None:
        self.task_panel = TaskPanel(self.task_manager, self)
        self.task_panel.cancel_requested.connect(self.task_manager.cancel)
        self.task_panel.clear_finished_requested.connect(self._clear_finished_tasks)
        self.task_panel.details_requested.connect(self.show_task_details)
        self.task_dock = QDockWidget("任务中心", self)
        self.task_dock.setObjectName("taskDock")
        self.task_dock.setWidget(self.task_panel)
        self.task_dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.task_dock)
        self.task_dock.visibilityChanged.connect(self._on_task_dock_visibility)
        self.task_dock_action.toggled.connect(self.task_dock.setVisible)
        self.task_dock.setVisible(self.settings.settings.task_panel_visible)

    # ------------------------------------------------------------ 主题与几何
    def _apply_saved_geometry(self) -> None:
        geometry = self.settings.settings.window_geometry
        if geometry:
            try:
                self.restoreGeometry(bytes.fromhex(geometry))
            except ValueError:
                logger.warning("窗口几何信息损坏，已使用默认大小")
        state = self.settings.settings.window_state
        if state:
            try:
                self.restoreState(bytes.fromhex(state))
            except ValueError:
                logger.warning("窗口状态信息损坏，已忽略")

    def _on_theme_selected(self) -> None:
        theme = str(self.theme_combo.currentData())
        self.settings.update(theme=theme)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, theme)

    def _on_task_dock_visibility(self, visible: bool) -> None:
        self.settings.update(task_panel_visible=visible)
        if self.task_dock_action.isChecked() != visible:
            self.task_dock_action.setChecked(visible)

    # ------------------------------------------------------------ 最近使用
    def _refresh_recent_menu(self) -> None:
        self.recent_menu.clear()
        entries = self.recent.items()
        if not entries:
            empty = self.recent_menu.addAction("（暂无记录）")
            empty.setEnabled(False)
            return
        for entry in entries:
            label = entry.display_name if entry.exists else f"{entry.display_name}（不存在）"
            action = self.recent_menu.addAction(label)
            action.setToolTip(str(entry.path))
            action.triggered.connect(lambda _checked=False, path=entry.path: self.open_archive(path))
        self.recent_menu.addSeparator()
        clear_action = self.recent_menu.addAction("清空最近记录")
        clear_action.triggered.connect(self._clear_recent)

    def _clear_recent(self) -> None:
        self.recent.clear()
        self.home_page.refresh()
        self._refresh_recent_menu()

    # ------------------------------------------------------------ 打开压缩包
    def _on_home_open_requested(self, path: Path) -> None:
        if path and str(path):
            self.open_archive(path)
        else:
            self.open_archive_dialog()

    def open_archive_dialog(self, *, for_extract: bool = False) -> None:
        patterns = " ".join(f"*{suffix}" for suffix in ArchiveFormat.all_suffixes())
        start_dir = self.settings.last_directory
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "选择压缩包",
            str(start_dir) if start_dir else str(Path.home()),
            f"压缩包 ({patterns});;所有文件 (*)",
        )
        if not selected:
            return
        if for_extract or len(selected) > 1:
            self.batch_extract(preselect=[Path(item) for item in selected])
            return
        self.open_archive(Path(selected[0]))

    def open_archive(self, path: Path, *, password: str | None = None) -> None:
        """打开压缩包并切换到浏览界面。"""

        path = Path(path)
        if not path.exists():
            QMessageBox.warning(self, "文件不存在", f"找不到文件：\n{path}")
            if self.current_archive is None:
                self.stack.setCurrentWidget(self.home_page)
            return
        if not is_archive_name(path.name) and ArchiveFormat.from_path(path) is None:
            QMessageBox.warning(
                self,
                "不支持的格式",
                f"PackPilot 不支持该文件类型：\n{path.name}\n\n"
                "支持的格式：" + "、".join(ArchiveFormat.all_suffixes()),
            )
            return

        password = password or self._password_cache.get(str(path).lower())
        try:
            info = self.manager.list_archive(path, password=password)
        except PasswordRequiredError:
            typed = PasswordDialog.ask(
                self,
                title="需要密码",
                description=f"{path.name} 已加密，请输入密码以浏览内容。",
            )
            if typed is None:
                return
            self._password_cache[str(path).lower()] = typed
            try:
                info = self.manager.list_archive(path, password=typed)
            except PackPilotError as exc:
                QMessageBox.critical(self, "打开失败", str(exc))
                return
            password = typed
        except UnsafeArchiveError as exc:
            QMessageBox.critical(self, "安全警告", str(exc))
            return
        except PackPilotError as exc:
            QMessageBox.critical(self, "打开失败", str(exc))
            return

        self.current_archive = path
        self._current_info = info
        self._current_password = password
        issues = self.manager.security_scan(path, password=password)
        if any(issue.level is IssueLevel.DANGER for issue in issues):
            self._warn_security(issues)
        self.archive_view.set_archive(info, issues=issues)
        self.stack.setCurrentWidget(self.archive_view)
        self.address_edit.setText(str(path))
        volume_note = f"（分卷 {len(info.volumes)} 卷）" if info.is_volume_set else ""
        self.breadcrumb_label.setText(f"压缩包浏览{volume_note}")
        self.setWindowTitle(f"{path.name} — {VERSION_DISPLAY}")
        self.recent.add(path)
        if path.parent:
            self.settings.remember_directory(path.parent)
        self.home_page.refresh()
        self._refresh_recent_menu()
        self.archive_opened.emit(path)
        self._update_actions()
        self.status_label.setText(f"已打开 {path.name}：{info.file_count} 个文件 / {info.dir_count} 个目录")
        logger.info("打开压缩包：%s", path)

    def _warn_security(self, issues: list[SecurityIssue]) -> None:
        details = describe_issues(issues)
        logger.warning("安全检查发现风险：%s", details)
        QMessageBox.warning(
            self,
            "安全警告",
            "压缩包存在安全风险，解压时危险条目将被拒绝：\n\n" + details,
        )

    def _on_address_entered(self) -> None:
        text = self.address_edit.text().strip()
        if not text:
            return
        path = Path(text)
        if path.exists():
            self.open_archive(path)
        elif path.parent.exists() and path.parent != path:
            self.open_archive(path.parent)
        else:
            QMessageBox.warning(self, "路径无效", f"路径不存在：\n{text}")

    def close_archive(self) -> None:
        self.current_archive = None
        self._current_info = None
        self._current_password = None
        self.archive_view.set_archive(None)
        self.stack.setCurrentWidget(self.home_page)
        self.address_edit.clear()
        self.breadcrumb_label.setText("主页")
        self.setWindowTitle(f"{VERSION_DISPLAY} — 压缩包管理器")
        self.home_page.refresh()
        self._update_actions()
        self.status_label.setText("已返回主页")

    def refresh_archive(self) -> None:
        if self.current_archive is None:
            self.home_page.refresh()
            self._refresh_recent_menu()
            return
        path = self.current_archive
        password = self._current_password
        self.open_archive(path, password=password)

    # ------------------------------------------------------------ 新建与压缩
    def new_archive(self, sources: list[Path] | None = None, target: Path | None = None) -> None:
        dialog = NewArchiveDialog(
            self,
            settings=self.settings,
            initial_sources=[Path(item) for item in (sources or [])],
            initial_target=target,
        )
        if dialog.exec() != NewArchiveDialog.DialogCode.Accepted:
            return
        options = dialog.create_options()
        self._submit_compress(options)

    def _submit_compress(self, options: CreateOptions) -> None:
        task = CompressTask(self.manager, options)
        self.task_manager.submit(task)
        self.task_dock.setVisible(True)
        self.status_label.setText(f"已加入压缩任务：{options.target.name}")

    def batch_compress(self, folders: list[Path] | None = None) -> None:
        dialog = BatchCompressDialog(
            self, settings=self.settings, folders=[Path(item) for item in (folders or [])]
        )
        if dialog.exec() != BatchCompressDialog.DialogCode.Accepted:
            return
        template = dialog.template()
        if dialog.merge_mode():
            merged = CreateOptions(
                target=template.target,
                sources=dialog.folders(),
                format=template.format,
                level=template.level,
                volume_size=template.volume_size,
                include_root=True,
            )
            self.task_manager.submit(MergeCompressTask(self.manager, merged))
        else:
            self.task_manager.submit(
                BatchCompressTask(
                    self.manager,
                    dialog.folders(),
                    target_dir=dialog.output_dir(),
                    template=template,
                )
            )
        self.task_dock.setVisible(True)
        self.status_label.setText("批量压缩任务已加入队列")

    # ------------------------------------------------------------ 解压
    def extract_entries(self, entries: list[str] | None) -> None:
        if self.current_archive is None:
            QMessageBox.information(self, "未打开压缩包", "请先打开一个压缩包。")
            return
        if self._current_info is None:
            return
        dialog = ExtractDialog(
            self,
            info=self._current_info,
            settings=self.settings,
            entries=entries,
            limits=self._safety_limits(),
            encrypted=self._current_info.encrypted,
        )
        if dialog.exec() != ExtractDialog.DialogCode.Accepted:
            return
        options = dialog.options()
        if options.password:
            self._password_cache[str(self.current_archive).lower()] = options.password
        task = ExtractTask(self.manager, self.current_archive, options)
        self.task_manager.submit(task)
        self.task_dock.setVisible(True)
        self.status_label.setText("已加入解压任务")

    def extract_to_current_folder(self) -> None:
        if self.current_archive is None or self._current_info is None:
            QMessageBox.information(self, "未打开压缩包", "请先打开一个压缩包。")
            return
        from app.core.smart_extract import plan_smart_extract

        plan = plan_smart_extract(self.current_archive, self._current_info)
        options = ExtractOptions(
            target=plan.directory,
            password=self._current_password,
            policy=OverwritePolicy.RENAME,
            smart=True,
            limits=self._safety_limits(),
        )
        try:
            self.manager.extract_archive(
                self.current_archive, options, progress=lambda *_: None
            )  # 预检查（磁盘空间/路径），立即返回错误
        except PackPilotError as exc:
            QMessageBox.critical(self, "无法解压", str(exc))
            return
        self.task_manager.submit(ExtractTask(self.manager, self.current_archive, options))
        self.task_dock.setVisible(True)
        self.status_label.setText(f"正在解压到 {plan.directory}")

    def batch_extract(self, preselect: list[Path] | None = None) -> None:
        dialog = BatchExtractDialog(
            self,
            settings=self.settings,
            archives=[Path(item) for item in (preselect or [])],
            limits=self._safety_limits(),
        )
        if dialog.exec() != BatchExtractDialog.DialogCode.Accepted:
            return
        task = BatchExtractTask(
            self.manager,
            dialog.archives(),
            target_dir=dialog.target_dir(),
            template=dialog.options_template(),
            use_stem_subdir=dialog.subdir_check.isChecked(),
        )
        self.task_manager.submit(task)
        self.task_dock.setVisible(True)
        self.status_label.setText("批量解压任务已加入队列")

    # ------------------------------------------------------------ 打开内部文件
    def open_entry(self, entry_name: str) -> None:
        if self.current_archive is None:
            return
        workspace = self.temp_manager.create_workspace("open")
        task = OpenEntryTask(
            self.manager,
            self.current_archive,
            entry_name,
            workspace,
            password=self._current_password,
        )
        self._pending_open[task.id] = (workspace, Path(entry_name))
        self.task_manager.submit(task)
        self.status_label.setText(f"正在解压并打开：{entry_name}")

    def _handle_open_entry_finished(self, task_id: str, info: TaskInfo) -> None:
        pending = self._pending_open.pop(task_id, None)
        if pending is None:
            return
        workspace, _entry = pending
        task = self.task_manager.task(task_id)
        if info.status is not TaskStatus.COMPLETED or task is None:
            self.temp_manager.schedule_cleanup(workspace, initial_delay_ms=500, max_attempts=5)
            if info.status is TaskStatus.FAILED:
                QMessageBox.warning(self, "无法打开文件", info.error or info.message)
            return
        target = task.result.payload if hasattr(task.result, "payload") else None
        if not isinstance(target, Path) or not target.exists():
            QMessageBox.warning(self, "无法打开文件", "解压后的临时文件不存在，可能已被清理。")
            self.temp_manager.schedule_cleanup(workspace, initial_delay_ms=500, max_attempts=5)
            return
        launched = open_with_default_program(
            target, on_exit=lambda: self.temp_manager.schedule_cleanup(workspace)
        )
        if not launched:
            QMessageBox.warning(
                self,
                "无法打开文件",
                f"系统默认程序启动失败。文件已解压到：\n{target}\n\n可以在资源管理器中手动打开。",
            )
            self.temp_manager.schedule_cleanup(workspace)
            return
        self.status_label.setText(f"已用默认程序打开 {target.name}（关闭程序后会自动清理临时文件）")

    # ------------------------------------------------------------ 条目操作
    def add_files_to_archive(self) -> None:
        if self.current_archive is None:
            QMessageBox.information(self, "未打开压缩包", "请先打开一个压缩包。")
            return
        files, _ = QFileDialog.getOpenFileNames(self, "选择要添加的文件", str(Path.home()))
        if not files:
            folder = QFileDialog.getExistingDirectory(self, "或选择要添加的文件夹", str(Path.home()))
            if not folder:
                return
            files = [folder]
        try:
            added = self.manager.add_files(
                self.current_archive,
                [Path(item) for item in files],
                password=self._current_password,
            )
        except PackPilotError as exc:
            QMessageBox.critical(self, "添加失败", str(exc))
            return
        self.status_label.setText(f"已添加 {len(added)} 个条目")
        self.refresh_archive()

    def delete_entries(self, entries: list[str] | None) -> None:
        if self.current_archive is None or not entries:
            QMessageBox.information(self, "未选择条目", "请先在列表中选中要删除的条目。")
            return
        if self.settings.settings.confirm_delete:
            names = "、".join(entries[:5])
            more = f" 等 {len(entries)} 项" if len(entries) > 5 else ""
            answer = QMessageBox.question(
                self,
                "删除条目",
                f"确定要从压缩包中删除 {names}{more} 吗？\n该操作会重写压缩包文件。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            removed = self.manager.delete_entries(
                self.current_archive, entries, password=self._current_password
            )
        except PackPilotError as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
            return
        self.status_label.setText(f"已删除 {removed} 个条目")
        self.refresh_archive()

    def copy_entry_paths(self, entries: list[str]) -> None:
        if not entries:
            return
        clipboard = QApplication.clipboard()
        clipboard.setText("\n".join(entries))
        self.status_label.setText(f"已复制 {len(entries)} 个条目路径")

    def show_entry_properties(self, entry_name: str) -> None:
        if self._current_info is None:
            return
        entry = self._current_info.entry_map().get(entry_name.rstrip("/"))
        if entry is None:
            return
        lines = [
            f"名称：{entry.name}",
            f"类型：{'文件夹' if entry.is_dir else '文件'}",
            f"原始大小：{human_size(entry.size)}",
            f"压缩后大小：{human_size(entry.compressed_size) if entry.compressed_size is not None else '未知（该格式不记录）'}",
            f"压缩率：{f'{entry.ratio * 100:.1f}%' if entry.ratio is not None else '-'}",
            f"修改时间：{entry.mtime.strftime('%Y-%m-%d %H:%M:%S') if entry.mtime else '-'}",
            f"CRC32：{f'{entry.crc:08X}' if entry.crc is not None else '-'}",
            f"加密：{'是' if entry.is_encrypted else '否'}",
            f"符号链接：{'是' if entry.is_symlink or entry.is_hardlink else '否'}",
        ]
        DetailsDialog(self, title=f"条目属性 — {entry.basename}", text="\n".join(lines)).exec()

    # ------------------------------------------------------------ 测试与扫描
    def test_archive(self) -> None:
        if self.current_archive is None:
            QMessageBox.information(self, "未打开压缩包", "请先打开一个压缩包。")
            return
        task = TestTask(self.manager, self.current_archive, password=self._current_password)
        self.task_manager.submit(task)
        self.task_dock.setVisible(True)
        self.status_label.setText("正在测试压缩包…")

    def scan_archive(self) -> None:
        if self.current_archive is None:
            QMessageBox.information(self, "未打开压缩包", "请先打开一个压缩包。")
            return
        task = ScanTask(self.manager, self.current_archive, password=self._current_password)
        self.task_manager.submit(task)
        self.task_dock.setVisible(True)
        self.status_label.setText("正在扫描压缩包…")

    def convert_archive(self) -> None:
        if self.current_archive is None:
            QMessageBox.information(self, "未打开压缩包", "请先打开一个压缩包。")
            return
        dialog = ConvertDialog(
            self,
            source=self.current_archive,
            settings=self.settings,
            encrypted=bool(self._current_info and self._current_info.encrypted),
        )
        if dialog.exec() != ConvertDialog.DialogCode.Accepted:
            return
        options = dialog.options()
        if options.source_password is None:
            options.source_password = self._current_password
        self.task_manager.submit(ConvertTask(self.manager, options))
        self.task_dock.setVisible(True)
        self.status_label.setText(f"正在转换：{options.source.name} → {options.target.name}")

    # ------------------------------------------------------------ 其它对话框
    def show_hash_dialog(self) -> None:
        paths: list[Path] = []
        if self.current_archive is not None:
            paths.append(self.current_archive)
        dialog = HashDialog(self, manager=self.task_manager, settings=self.settings, paths=paths)
        dialog.exec()

    def show_history(self) -> None:
        HistoryDialog(self, history=self.history).exec()

    def show_settings(self) -> None:
        dialog = SettingsDialog(self, settings=self.settings)
        dialog.theme_changed.connect(self._on_theme_changed)
        dialog.associations_changed.connect(self._update_actions)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            self.manager.limits = self._safety_limits()
            self.task_dock.setVisible(self.settings.settings.task_panel_visible)
            self._on_theme_changed(self.settings.settings.theme)

    def _on_theme_changed(self, theme: str) -> None:
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, theme)
        index = self.theme_combo.findData(theme)
        if index >= 0 and index != self.theme_combo.currentIndex():
            self.theme_combo.setCurrentIndex(index)

    def show_about(self) -> None:
        AboutDialog(self).exec()

    def _show_shortcuts(self) -> None:
        text = (
            "Ctrl + N    新建压缩包\n"
            "Ctrl + O    打开压缩包\n"
            "Ctrl + E    解压\n"
            "Ctrl + A    添加文件\n"
            "Ctrl + F    搜索\n"
            "Ctrl + T    测试压缩包\n"
            "Ctrl + H    计算哈希\n"
            "Ctrl + W    关闭当前压缩包\n"
            "F5          刷新\n"
            "Delete      删除选中条目\n"
            "Esc         取消最近的任务\n"
            "Ctrl + Q    退出"
        )
        DetailsDialog(self, title="快捷键说明", text=text, height=340).exec()

    # ------------------------------------------------------------ Windows 集成
    def _install_associations(self) -> None:
        registered = file_association.install_file_associations()
        if registered:
            self.settings.update(file_association_installed=True)
            QMessageBox.information(self, "文件关联", "已注册扩展名：\n" + "、".join(registered))
        else:
            QMessageBox.warning(self, "文件关联", "当前系统不支持写入注册表（仅支持 Windows）。")

    def _remove_associations(self) -> None:
        file_association.remove_file_associations()
        self.settings.update(file_association_installed=False)
        QMessageBox.information(self, "文件关联", "已移除 PackPilot 文件关联。")

    def _install_menu(self) -> None:
        if context_menu.install_context_menu():
            self.settings.update(context_menu_installed=True)
            QMessageBox.information(self, "右键菜单", "已安装右键菜单。")
        else:
            QMessageBox.warning(self, "右键菜单", "当前系统不支持写入注册表（仅支持 Windows）。")

    def _remove_menu(self) -> None:
        context_menu.remove_context_menu()
        self.settings.update(context_menu_installed=False)
        QMessageBox.information(self, "右键菜单", "已移除右键菜单。")

    def _show_integration_status(self) -> None:
        commands = "\n".join(f"{label}：{command}" for label, command in context_menu.preview_commands())
        text = (
            f"文件关联：{file_association.association_summary()}\n"
            f"右键菜单：{context_menu.context_menu_summary()}\n\n"
            f"右键菜单实际调用命令：\n{commands}\n\n"
            f"{context_menu.explorer_restart_hint()}"
        )
        DetailsDialog(self, title="Windows 集成状态", text=text).exec()

    # ------------------------------------------------------------ 任务
    def _connect_tasks(self) -> None:
        self.task_manager.task_updated.connect(self._on_task_updated)
        self.task_manager.task_finished.connect(self._on_task_finished)
        self.task_manager.tasks_changed.connect(self._refresh_task_count)

    def _on_task_updated(self, _task_id: str, info: object) -> None:
        if not isinstance(info, TaskInfo):
            return
        if info.status in {TaskStatus.RUNNING, TaskStatus.PENDING}:
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(info.percent)
            self.progress_bar.setFormat(f"{info.percent}%  {info.kind.label}")
            message = info.current_file or info.message
            self.status_label.setText(f"{info.title}：{message}" if message else info.title)
            self.cancel_button.setVisible(True)

    def _on_task_finished(self, task_id: str, info: object) -> None:
        if not isinstance(info, TaskInfo):
            return
        self._handle_open_entry_finished(task_id, info)
        if info.status is TaskStatus.COMPLETED:
            self.status_label.setText(f"{info.title}：{info.message}")
            if info.kind.label == "压缩":
                self.home_page.refresh()
        elif info.status is TaskStatus.FAILED:
            self.status_label.setText(f"{info.title} 失败：{info.error or info.message}")
            QMessageBox.critical(
                self,
                f"任务失败：{info.kind.label}",
                f"{info.title}\n\n{info.error or info.message}\n\n详细信息可在任务中心双击查看。",
            )
        elif info.status is TaskStatus.CANCELLED:
            self.status_label.setText(f"{info.title}：已取消")
        self._refresh_task_count()

    def _refresh_task_count(self) -> None:
        active = self.task_manager.active_count()
        total = len(self.task_manager.all_tasks())
        self.task_count_label.setText(f"任务：{active}/{total}")
        if active == 0:
            self.progress_bar.setVisible(False)
            self.cancel_button.setVisible(False)

    def _clear_finished_tasks(self) -> None:
        removed = self.task_manager.clear_finished()
        self.status_label.setText(f"已清除 {removed} 条任务记录")

    def show_task_details(self, task_id: str) -> None:
        task = self.task_manager.task(task_id)
        info = self.task_manager.info(task_id)
        if info is None:
            return
        detail = ""
        if task is not None and hasattr(task.result, "detail"):
            detail = str(task.result.detail or "")
        text = (
            f"任务：{info.title}\n"
            f"类型：{info.kind.label}\n"
            f"状态：{info.status_label}\n"
            f"进度：{info.percent}%\n"
            f"源：{info.source or '-'}\n"
            f"目标：{info.target or '-'}\n"
            f"开始时间：{info.started_at.strftime('%Y-%m-%d %H:%M:%S') if info.started_at else '-'}\n"
            f"耗时：{info.elapsed:.1f} 秒\n"
            f"结果：{info.message}\n"
        )
        if info.error:
            text += f"错误：{info.error}\n"
        if detail:
            text += f"\n详细信息：\n{detail}\n"
        DetailsDialog(self, title=f"任务详情 — {info.title}", text=text).exec()

    # ------------------------------------------------------------ 拖放
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if not paths:
            return
        event.acceptProposedAction()
        self._handle_dropped_paths(paths)

    def _handle_dropped_paths(self, paths: list[Path]) -> None:
        archives = [path for path in paths if path.is_file() and is_archive_name(path.name)]
        others = [path for path in paths if path not in archives]
        if archives and not others:
            if len(archives) == 1:
                self.open_archive(archives[0])
                return
            answer = QMessageBox.question(
                self,
                "批量操作",
                f"拖入了 {len(archives)} 个压缩包：\n\n是：批量解压\n否：仅打开第一个",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.batch_extract(preselect=archives)
            else:
                self.open_archive(archives[0])
            return
        if self.current_archive is not None:
            answer = QMessageBox.question(
                self,
                "添加到压缩包",
                f"已打开 {self.current_archive.name}。\n\n是：添加到当前压缩包\n否：新建压缩包",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.Yes:
                try:
                    added = self.manager.add_files(
                        self.current_archive, paths, password=self._current_password
                    )
                except PackPilotError as exc:
                    QMessageBox.critical(self, "添加失败", str(exc))
                    return
                self.status_label.setText(f"已添加 {len(added)} 个条目")
                self.refresh_archive()
                return
        self.new_archive(sources=paths)

    # ------------------------------------------------------------ 其它
    def _startup_cleanup(self) -> None:
        removed = self.temp_manager.cleanup_stale()
        if removed:
            logger.info("启动时清理了 %d 个残留临时目录", removed)
        if self.manager is not None and self.current_archive is None:
            self._update_actions()

    def _update_actions(self) -> None:
        has_archive = self.current_archive is not None
        for action in (
            self.action_extract,
            self.action_extract_here,
            self.action_add,
            self.action_delete,
            self.action_test,
            self.action_scan,
            self.action_convert,
            self.action_close_archive,
            self.action_refresh,
        ):
            action.setEnabled(has_archive)
        self.action_search.setEnabled(has_archive)

    def closeEvent(self, event: QCloseEvent) -> None:
        active = self.task_manager.active_count()
        if active:
            answer = QMessageBox.question(
                self,
                "仍有任务运行",
                f"当前有 {active} 个任务正在运行，退出会取消这些任务。是否继续退出？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.task_manager.cancel_all()
            self.task_manager.wait_for_done(5000)
        self.settings.update(
            window_geometry=bytes(self.saveGeometry()).hex(),
            window_state=bytes(self.saveState()).hex(),
            task_panel_visible=self.task_dock.isVisible(),
        )
        removed = self.temp_manager.cleanup_all()
        if removed:
            logger.info("退出时清理了 %d 个临时目录", removed)
        logger.info("PackPilot 退出")
        super().closeEvent(event)


def create_main_window(argv: list[str] | None = None) -> MainWindow:
    """创建主窗口并完成日志初始化（供 main.py 与测试使用）。"""

    setup_logging()
    return MainWindow()
