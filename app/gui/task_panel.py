"""任务中心面板：任务列表、进度、状态与取消操作。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.utils import human_duration, human_speed
from app.tasks.base_task import TaskInfo, TaskStatus
from app.tasks.task_manager import TaskManager

COL_TASK = 0
COL_STATUS = 1
COL_PROGRESS = 2
COL_FILE = 3
COL_SPEED = 4
COL_ETA = 5
COL_ACTION = 6

STATUS_COLORS = {
    TaskStatus.COMPLETED.value: "#16a34a",
    TaskStatus.FAILED.value: "#dc2626",
    TaskStatus.CANCELLED.value: "#6b7280",
    TaskStatus.RUNNING.value: "#2563eb",
    TaskStatus.PENDING.value: "#b45309",
}


class TaskPanel(QWidget):
    """展示 TaskManager 中所有任务的表格。"""

    cancel_requested = Signal(str)
    clear_finished_requested = Signal()
    details_requested = Signal(str)

    def __init__(self, manager: TaskManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(["任务", "状态", "进度", "当前文件", "速度", "剩余时间", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.itemDoubleClicked.connect(self._on_double_click)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_TASK, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_PROGRESS, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_PROGRESS, 160)
        header.setSectionResizeMode(COL_FILE, QHeaderView.ResizeMode.Stretch)
        for column in (COL_SPEED, COL_ETA, COL_ACTION):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

        self.empty_label = QLabel("暂无任务", self)
        self.empty_label.setStyleSheet("color: #6b7280;")
        self.summary_label = QLabel("", self)
        clear_button = QPushButton("清除已完成", self)
        clear_button.clicked.connect(self.clear_finished_requested.emit)
        cancel_all_button = QPushButton("全部取消", self)
        cancel_all_button.clicked.connect(self.manager.cancel_all)
        self.cancel_all_button = cancel_all_button

        footer = QHBoxLayout()
        footer.addWidget(self.summary_label)
        footer.addStretch(1)
        footer.addWidget(clear_button)
        footer.addWidget(cancel_all_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.empty_label)
        layout.addLayout(footer)

        manager.task_added.connect(self._on_task_added)
        manager.task_updated.connect(self._on_task_updated)
        manager.task_finished.connect(lambda _task_id, _info: self.refresh())
        manager.tasks_changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """按 TaskManager 状态重建表格。"""

        tasks = self.manager.all_tasks()
        self.table.setRowCount(len(tasks))
        for row, info in enumerate(tasks):
            self._fill_row(row, info)
        self.empty_label.setVisible(not tasks)
        self.table.setVisible(bool(tasks))
        active = sum(1 for info in tasks if info.is_active)
        self.summary_label.setText(f"任务 {len(tasks)} 个，进行中 {active} 个")
        self.cancel_all_button.setEnabled(active > 0)

    def _fill_row(self, row: int, info: TaskInfo) -> None:
        title = QTableWidgetItem(f"[{info.kind.label}] {info.title}")
        title.setData(Qt.ItemDataRole.UserRole, info.id)
        title.setToolTip(
            f"源：{info.source or '-'}\n目标：{info.target or '-'}"
            + (f"\n错误：{info.error}" if info.error else "")
        )
        self.table.setItem(row, COL_TASK, title)

        status = QTableWidgetItem(info.status_label)
        status.setForeground(Qt.GlobalColor.darkGreen)
        color = STATUS_COLORS.get(info.status.value, "")
        if color:
            from PySide6.QtGui import QColor

            status.setForeground(QColor(color))
        self.table.setItem(row, COL_STATUS, status)

        bar = QProgressBar(self.table)
        bar.setRange(0, 100)
        bar.setValue(info.percent)
        bar.setFormat(f"{info.percent}%")
        if info.status is TaskStatus.FAILED:
            bar.setStyleSheet("QProgressBar::chunk { background: #dc2626; }")
        elif info.status is TaskStatus.COMPLETED:
            bar.setStyleSheet("QProgressBar::chunk { background: #16a34a; }")
        self.table.setCellWidget(row, COL_PROGRESS, bar)

        file_item = QTableWidgetItem(info.current_file or info.message)
        file_item.setToolTip(info.current_file or info.message)
        self.table.setItem(row, COL_FILE, file_item)

        speed = QTableWidgetItem(human_speed(info.speed_bps) if info.status is TaskStatus.RUNNING else "-")
        self.table.setItem(row, COL_SPEED, speed)
        eta = QTableWidgetItem(
            human_duration(info.eta_seconds) if info.is_active and info.percent >= 5 else "-"
        )
        self.table.setItem(row, COL_ETA, eta)

        if info.is_active:
            button = QPushButton("取消", self.table)
            button.clicked.connect(
                lambda _checked=False, task_id=info.id: self.cancel_requested.emit(task_id)
            )
            self.table.setCellWidget(row, COL_ACTION, button)
        else:
            label = QLabel("详情", self.table)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setToolTip("双击查看详情")
            self.table.setCellWidget(row, COL_ACTION, label)

    def _on_task_added(self, _task_id: str) -> None:
        self.refresh()

    def _on_task_updated(self, _task_id: str, _info: object) -> None:
        self.refresh()

    def _selected_task_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, COL_TASK)
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value else None

    def _on_double_click(self, _item: QTableWidgetItem) -> None:
        task_id = self._selected_task_id()
        if task_id:
            self.details_requested.emit(task_id)

    def _on_context_menu(self, position) -> None:  # type: ignore[no-untyped-def]
        from PySide6.QtWidgets import QMenu

        task_id = self._selected_task_id()
        if task_id is None:
            return
        info = self.manager.info(task_id)
        if info is None:
            return
        menu = QMenu(self)
        if info.is_active:
            menu.addAction("取消任务", lambda: self.cancel_requested.emit(task_id))
        else:
            menu.addAction("查看详情", lambda: self.details_requested.emit(task_id))
            menu.addAction("从列表移除", lambda: (self.manager.remove(task_id), self.refresh()))
        menu.exec(self.table.viewport().mapToGlobal(position))
