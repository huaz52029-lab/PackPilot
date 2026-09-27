"""压缩包浏览视图：目录树、搜索、排序、多选与右键菜单。"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QStackedWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.archive_info import ArchiveEntry, ArchiveInfo
from app.core.archive_security import IssueLevel, SecurityIssue
from app.core.utils import format_timestamp, human_size

COL_NAME = 0
COL_TYPE = 1
COL_SIZE = 2
COL_COMPRESSED = 3
COL_RATIO = 4
COL_MTIME = 5


class EntryItem(QTreeWidgetItem):
    """条目节点：保存压缩包内路径并支持按数值排序。"""

    def __init__(self, entry: ArchiveEntry | None, name: str, *, is_dir: bool) -> None:
        super().__init__()
        self.entry = entry
        self.is_dir = is_dir
        self.setIcon(COL_NAME, QIconFactory.folder() if is_dir else QIconFactory.file())
        self.setText(COL_NAME, name)
        if entry is not None:
            self.setText(
                COL_TYPE,
                "文件夹" if entry.is_dir else PurePosixPath(entry.name).suffix.lstrip(".").upper() or "文件",
            )
            self.setText(COL_SIZE, "-" if entry.is_dir else human_size(entry.size))
            self.setText(
                COL_COMPRESSED,
                "-" if entry.is_dir or entry.compressed_size is None else human_size(entry.compressed_size),
            )
            self.setText(COL_RATIO, "-" if entry.ratio is None else f"{entry.ratio * 100:.1f}%")
            self.setText(COL_MTIME, format_timestamp(entry.mtime))

    def __lt__(self, other: QTreeWidgetItem) -> bool:  # 排序：目录优先，其次按列类型
        tree = self.treeWidget()
        column = tree.sortColumn() if tree is not None else COL_NAME
        left_dir = self.is_dir
        right_dir = getattr(other, "is_dir", False)
        if column == COL_NAME and left_dir != right_dir:
            return left_dir
        if column == COL_SIZE:
            return self._size_value() < getattr(other, "_size_value", lambda: 0)()
        if column == COL_COMPRESSED:
            left = self.entry.compressed_size if self.entry and self.entry.compressed_size else -1
            right = (
                other.entry.compressed_size
                if getattr(other, "entry", None) and other.entry.compressed_size
                else -1
            )
            return left < right
        if column == COL_RATIO:
            left = self.entry.ratio if self.entry and self.entry.ratio is not None else -1
            right = (
                other.entry.ratio if getattr(other, "entry", None) and other.entry.ratio is not None else -1
            )
            return left < right
        if column == COL_MTIME:
            left = self.entry.mtime.timestamp() if self.entry and self.entry.mtime else 0
            right = (
                other.entry.mtime.timestamp() if getattr(other, "entry", None) and other.entry.mtime else 0
            )
            return left < right
        return self.text(column).lower() < other.text(column).lower()

    def _size_value(self) -> int:
        return self.entry.size if self.entry and not self.entry.is_dir else 0

    def path(self) -> str:
        """返回该节点在压缩包内的完整路径。"""

        parts: list[str] = []
        node: QTreeWidgetItem | None = self
        while isinstance(node, EntryItem):
            parts.append(node.text(COL_NAME))
            node = node.parent()
        return "/".join(reversed(parts))


class QIconFactory:
    """统一的图标获取（使用系统标准图标，避免额外资源依赖）。"""

    @staticmethod
    def _icon(standard):  # type: ignore[no-untyped-def]
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            from PySide6.QtGui import QIcon

            return QIcon()
        return app.style().standardIcon(standard)

    @classmethod
    def folder(cls):  # type: ignore[no-untyped-def]
        from PySide6.QtWidgets import QStyle

        return cls._icon(QStyle.StandardPixmap.SP_DirIcon)

    @classmethod
    def file(cls):  # type: ignore[no-untyped-def]
        from PySide6.QtWidgets import QStyle

        return cls._icon(QStyle.StandardPixmap.SP_FileIcon)

    @classmethod
    def archive(cls):  # type: ignore[no-untyped-def]
        from PySide6.QtWidgets import QStyle

        return cls._icon(QStyle.StandardPixmap.SP_DriveHDIcon)


class ArchiveView(QWidget):
    """显示当前压缩包内容。"""

    open_entry_requested = Signal(str)
    extract_selected_requested = Signal(list)
    extract_all_requested = Signal()
    delete_requested = Signal(list)
    copy_path_requested = Signal(list)
    properties_requested = Signal(str)
    refresh_requested = Signal()
    add_files_requested = Signal()
    test_requested = Signal()
    security_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._info: ArchiveInfo | None = None

        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("在压缩包内搜索（Ctrl+F）")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        self.search_edit.setMaximumWidth(320)

        self.toolbar = QToolBar(self)
        self.toolbar.setIconSize(self.toolbar.iconSize())
        self.refresh_action = QAction("刷新 (F5)", self)
        self.refresh_action.triggered.connect(self.refresh_requested.emit)
        self.add_action = QAction("添加文件", self)
        self.add_action.triggered.connect(self.add_files_requested.emit)
        self.extract_action = QAction("解压选中项", self)
        self.extract_action.triggered.connect(
            lambda: self.extract_selected_requested.emit(self.selected_paths())
        )
        self.extract_all_action = QAction("全部解压", self)
        self.extract_all_action.triggered.connect(self.extract_all_requested.emit)
        self.test_action = QAction("测试", self)
        self.test_action.triggered.connect(self.test_requested.emit)
        self.scan_action = QAction("安全检查", self)
        self.scan_action.triggered.connect(self.security_requested.emit)
        self.delete_action = QAction("删除条目", self)
        self.delete_action.triggered.connect(lambda: self.delete_requested.emit(self.selected_paths()))
        for action in (
            self.refresh_action,
            self.add_action,
            self.extract_action,
            self.extract_all_action,
            self.test_action,
            self.scan_action,
            self.delete_action,
        ):
            self.toolbar.addAction(action)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(["名称", "类型", "原始大小", "压缩大小", "压缩率", "修改时间"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(COL_NAME, Qt.SortOrder.AscendingOrder)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.itemSelectionChanged.connect(self._update_actions)
        header = self.tree.header()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for column in (COL_TYPE, COL_SIZE, COL_COMPRESSED, COL_RATIO, COL_MTIME):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSortIndicatorShown(True)

        self.summary_label = QLabel("尚未打开压缩包", self)
        self.summary_label.setStyleSheet("color: #6b7280;")
        self.issue_label = QLabel("", self)
        self.issue_label.setWordWrap(True)
        self.issue_label.setVisible(False)
        self.issue_label.setStyleSheet("color: #b91c1c;")

        empty_page = QWidget(self)
        empty_layout = QVBoxLayout(empty_page)
        hint = QLabel("拖入压缩包，或使用 Ctrl+O 打开文件", empty_page)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #6b7280; font-size: 14px;")
        empty_layout.addStretch(1)
        empty_layout.addWidget(hint)
        empty_layout.addStretch(1)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(empty_page)
        self.stack.addWidget(self.tree)

        toolbar_row = QWidget(self)
        from PySide6.QtWidgets import QHBoxLayout

        toolbar_layout = QHBoxLayout(toolbar_row)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.addWidget(self.toolbar)
        toolbar_layout.addStretch(1)
        toolbar_layout.addWidget(self.search_edit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.addWidget(toolbar_row)
        layout.addWidget(self.issue_label)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.summary_label)
        self._update_actions()

    # ------------------------------------------------------------------
    def set_archive(self, info: ArchiveInfo | None, *, issues: Iterable[SecurityIssue] = ()) -> None:
        """显示新的压缩包内容。"""

        self._info = info
        self.tree.clear()
        if info is None:
            self.stack.setCurrentIndex(0)
            self.summary_label.setText("尚未打开压缩包")
            self.issue_label.setVisible(False)
            self._update_actions()
            return

        nodes: dict[str, EntryItem] = {}
        for entry in sorted(info.entries, key=lambda item: (item.name.count("/"), item.name)):
            normalized = entry.name.rstrip("/")
            if not normalized:
                continue
            parts = normalized.split("/")
            parent: QTreeWidgetItem | EntryItem = self.tree.invisibleRootItem()
            current_path = ""
            for index, part in enumerate(parts):
                current_path = f"{current_path}/{part}" if current_path else part
                is_last = index == len(parts) - 1
                existing = nodes.get(current_path)
                if existing is not None:
                    parent = existing
                    continue
                entry_for_node = entry if is_last else None
                item = EntryItem(entry_for_node, part, is_dir=(not is_last) or entry.is_dir)
                if isinstance(parent, EntryItem):
                    parent.addChild(item)
                else:
                    self.tree.addTopLevelItem(item)
                nodes[current_path] = item
                parent = item
            if entry.mtime is None and nodes.get(normalized) is not None:
                nodes[normalized].setText(COL_MTIME, "-")

        self.stack.setCurrentIndex(1)
        self.tree.expandToDepth(0)
        self._describe(info)
        self._show_issues(list(issues))
        self._update_actions()

    def _describe(self, info: ArchiveInfo) -> None:
        parts = [
            f"格式：{info.format.display_name}",
            f"文件：{info.file_count}",
            f"目录：{info.dir_count}",
            f"原始大小：{human_size(info.total_size)}",
            f"压缩后：{human_size(info.total_compressed_size)}",
        ]
        if info.compression_ratio is not None:
            parts.append(f"压缩率：{info.compression_ratio * 100:.1f}%")
        if info.encrypted:
            parts.append("已加密")
        if info.is_volume_set:
            parts.append(f"分卷：{len(info.volumes)} 卷")
        self.summary_label.setText("    ".join(parts))

    def _show_issues(self, issues: list[SecurityIssue]) -> None:
        if not issues:
            self.issue_label.setVisible(False)
            return
        danger = [issue for issue in issues if issue.level is IssueLevel.DANGER]
        text = "；".join(str(issue) for issue in (danger or issues)[:3])
        self.issue_label.setText(("⚠ " if danger else "提示：") + text)
        self.issue_label.setVisible(True)
        self.issue_label.setStyleSheet("color: #b91c1c;" if danger else "color: #b45309;")

    def info(self) -> ArchiveInfo | None:
        return self._info

    def refresh_requested_emit(self) -> None:  # pragma: no cover - 兼容旧接口
        self.refresh_requested.emit()

    # ------------------------------------------------------------------
    def selected_paths(self) -> list[str]:
        paths: list[str] = []
        for item in self.tree.selectedItems():
            if isinstance(item, EntryItem):
                path = item.path()
                if path:
                    paths.append(path)
        return paths

    def selected_entries(self) -> list[ArchiveEntry]:
        if self._info is None:
            return []
        mapping = {entry.name.rstrip("/"): entry for entry in self._info.entries}
        result: list[ArchiveEntry] = []
        for path in self.selected_paths():
            entry = mapping.get(path)
            if entry is not None:
                result.append(entry)
        return result

    def _update_actions(self) -> None:
        has_archive = self._info is not None
        selected = bool(self.selected_paths())
        self.add_action.setEnabled(has_archive)
        self.extract_action.setEnabled(has_archive)
        self.extract_all_action.setEnabled(has_archive)
        self.test_action.setEnabled(has_archive)
        self.scan_action.setEnabled(has_archive)
        self.delete_action.setEnabled(has_archive and selected)
        for action in (
            self.refresh_action,
            self.add_action,
            self.extract_action,
            self.extract_all_action,
            self.test_action,
            self.scan_action,
            self.delete_action,
        ):
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        del has_archive

    def focus_search(self) -> None:
        self.search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search_edit.selectAll()

    def _apply_filter(self, text: str) -> None:
        keyword = text.strip().lower()
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if isinstance(item, EntryItem):
                self._filter_item(item, keyword)

    def _filter_item(self, item: EntryItem, keyword: str) -> bool:
        """返回该节点是否应显示（子节点匹配时父节点保留）。"""

        visible = keyword in item.text(COL_NAME).lower() or keyword in item.path().lower()
        for index in range(item.childCount()):
            child = item.child(index)
            if isinstance(child, EntryItem) and self._filter_item(child, keyword):
                visible = True
        item.setHidden(not visible)
        if keyword and visible and item.childCount():
            item.setExpanded(True)
        return visible

    # ------------------------------------------------------------------
    def _on_double_click(self, item: QTreeWidgetItem, _column: int) -> None:
        if not isinstance(item, EntryItem):
            return
        if item.is_dir:
            item.setExpanded(not item.isExpanded())
            return
        path = item.path()
        if path:
            self.open_entry_requested.emit(path)

    def _show_context_menu(self, position) -> None:  # type: ignore[no-untyped-def]
        item = self.tree.itemAt(position)
        menu = QMenu(self)
        if isinstance(item, EntryItem) and not item.is_dir:
            menu.addAction("打开", lambda: self.open_entry_requested.emit(item.path()))
            menu.addAction("解压选中项", lambda: self.extract_selected_requested.emit(self.selected_paths()))
        elif isinstance(item, EntryItem):
            menu.addAction("解压该目录", lambda: self.extract_selected_requested.emit(self.selected_paths()))
        menu.addSeparator()
        menu.addAction("全部解压", self.extract_all_requested.emit)
        menu.addAction("添加文件", self.add_files_requested.emit)
        menu.addAction("删除条目", lambda: self.delete_requested.emit(self.selected_paths()))
        menu.addSeparator()
        menu.addAction("复制路径", lambda: self.copy_path_requested.emit(self.selected_paths()))
        if isinstance(item, EntryItem):
            menu.addAction("属性", lambda: self.properties_requested.emit(item.path()))
        menu.addSeparator()
        menu.addAction("刷新", self.refresh_requested.emit)
        menu.addAction("测试压缩包", self.test_requested.emit)
        menu.addAction("安全检查", self.security_requested.emit)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_path_requested.emit(self.selected_paths())
            return
        if event.key() == Qt.Key.Key_Delete:
            self.delete_requested.emit(self.selected_paths())
            return
        super().keyPressEvent(event)
