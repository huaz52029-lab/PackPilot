"""主页：最近使用列表、快捷操作与拖放提示。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.utils import format_timestamp, human_size
from app.services.recent_files import RecentEntry, RecentFilesService
from app.version import VERSION_DISPLAY


class HomePage(QWidget):
    """未打开压缩包时显示的起始页面。"""

    open_requested = Signal(Path)
    new_requested = Signal()
    extract_requested = Signal()
    batch_compress_requested = Signal()
    batch_extract_requested = Signal()
    hash_requested = Signal()
    clear_recent_requested = Signal()

    def __init__(self, recent: RecentFilesService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._recent = recent
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        title = QLabel(VERSION_DISPLAY, self)
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        subtitle = QLabel(
            "Windows 压缩包管理器：双击列表中的压缩包即可浏览，或把文件拖入窗口。",
            self,
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #6b7280;")

        recent_title = QLabel("最近使用", self)
        recent_title.setStyleSheet("font-weight: 600; margin-top: 8px;")
        self.recent_list = QListWidget(self)
        self.recent_list.setAlternatingRowColors(True)
        self.recent_list.itemActivated.connect(self._on_item_activated)
        self.recent_list.itemDoubleClicked.connect(self._on_item_activated)
        self.recent_list.setToolTip("双击打开，右键可移除记录")
        self.recent_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.recent_list.customContextMenuRequested.connect(self._on_context_menu)
        self.recent_list.setMinimumHeight(160)

        clear_button = QPushButton("清空最近记录", self)
        clear_button.clicked.connect(self.clear_recent_requested.emit)

        buttons: list[tuple[str, Callable[[], None]]] = [
            ("新建压缩包 (Ctrl+N)", self.new_requested.emit),
            ("打开压缩包 (Ctrl+O)", self._emit_open_dialog),
            ("解压压缩包 (Ctrl+E)", self.extract_requested.emit),
            ("批量压缩", self.batch_compress_requested.emit),
            ("批量解压", self.batch_extract_requested.emit),
            ("计算哈希", self.hash_requested.emit),
        ]
        button_row = QVBoxLayout()
        button_row.setSpacing(6)
        for text, callback in buttons:
            button = QPushButton(text, self)
            button.setMinimumHeight(30)
            button.clicked.connect(lambda _checked=False, cb=callback: cb())
            button_row.addWidget(button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(6)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(8)
        layout.addWidget(recent_title)
        layout.addWidget(self.recent_list, 1)
        layout.addWidget(clear_button)
        layout.addSpacing(6)
        layout.addLayout(button_row)
        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """重新载入最近使用列表。"""

        self.recent_list.clear()
        entries = self._recent.items()
        if not entries:
            placeholder = QListWidgetItem("（暂无记录）")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent_list.addItem(placeholder)
            return
        for entry in entries:
            item = QListWidgetItem(self._format_entry(entry))
            item.setData(Qt.ItemDataRole.UserRole, str(entry.path))
            if not entry.exists:
                item.setForeground(Qt.GlobalColor.gray)
                item.setToolTip("文件不存在（可能已被移动或删除）")
            else:
                item.setToolTip(str(entry.path))
            self.recent_list.addItem(item)

    @staticmethod
    def _format_entry(entry: RecentEntry) -> str:
        status = "" if entry.exists else "  [文件不存在]"
        return (
            f"{entry.display_name}{status}    {human_size(entry.size)}    {format_timestamp(entry.opened_at)}"
        )

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        raw = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(raw, str) and raw:
            self.open_requested.emit(Path(raw))

    def _emit_open_dialog(self) -> None:
        self.open_requested.emit(Path())

    def _on_context_menu(self, position) -> None:  # type: ignore[no-untyped-def]
        item = self.recent_list.itemAt(position)
        if item is None:
            return
        raw = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(raw, str) or not raw:
            return
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        open_action = menu.addAction("打开")
        open_folder_action = menu.addAction("打开所在文件夹")
        remove_action = menu.addAction("从列表移除")
        chosen = menu.exec(self.recent_list.mapToGlobal(position))
        if chosen is open_action:
            self.open_requested.emit(Path(raw))
        elif chosen is open_folder_action:
            from app.windows.shell_execute import reveal_in_explorer

            reveal_in_explorer(Path(raw))
        elif chosen is remove_action:
            self._recent.remove(Path(raw))
            self.refresh()
