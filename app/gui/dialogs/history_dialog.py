"""任务历史对话框。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.utils import human_duration
from app.services.history import HistoryService


class HistoryDialog(QDialog):
    """展示任务历史，支持导出与清空。"""

    def __init__(self, parent: QWidget | None = None, *, history: HistoryService) -> None:
        super().__init__(parent)
        self.setWindowTitle("任务历史")
        self.resize(900, 520)
        self.history = history

        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(["时间", "任务类型", "标题", "源", "目标", "结果", "耗时"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        for column in range(4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        self.summary_label = QLabel(self)
        export_button = QPushButton("导出 CSV/TXT…", self)
        export_button.clicked.connect(self._export)
        clear_button = QPushButton("清空历史", self)
        clear_button.clicked.connect(self._clear)
        refresh_button = QPushButton("刷新", self)
        refresh_button.clicked.connect(self.reload)
        row = QHBoxLayout()
        row.addWidget(self.summary_label)
        row.addStretch(1)
        row.addWidget(refresh_button)
        row.addWidget(export_button)
        row.addWidget(clear_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addLayout(row)
        layout.addWidget(buttons)
        self.reload()

    def reload(self) -> None:
        records = self.history.records()
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            values = [
                record.timestamp,
                record.kind,
                record.title,
                record.source,
                record.target,
                record.status + (f"（{record.error}）" if record.error else ""),
                human_duration(record.duration),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(record.detail or value)
                self.table.setItem(row, column, item)
        self.summary_label.setText(f"共 {len(records)} 条记录")

    def _export(self) -> None:
        target, _ = QFileDialog.getSaveFileName(
            self, "导出任务历史", str(Path.home() / "packpilot-history.txt"), "文本文件 (*.txt)"
        )
        if not target:
            return
        try:
            self.history.export(Path(target))
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", f"已导出到：\n{target}")

    def _clear(self) -> None:
        answer = QMessageBox.question(
            self,
            "清空历史",
            "确定要清空全部任务历史吗？该操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.history.clear()
            self.reload()
