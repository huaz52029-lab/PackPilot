"""哈希计算对话框：多文件、多算法、复制与导出。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.archive_info import ArchiveFormat
from app.core.checksum import ALGORITHMS, HashResult, export_results, results_to_text
from app.core.utils import human_size
from app.services.settings import SettingsService
from app.tasks.base_task import TaskStatus
from app.tasks.hash_task import HashTask
from app.tasks.task_manager import TaskManager


class HashDialog(QDialog):
    """选择文件与算法，通过任务中心计算哈希。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        manager: TaskManager,
        settings: SettingsService,
        paths: list[Path] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("计算哈希")
        self.resize(760, 560)
        self.manager = manager
        self.settings = settings
        self.results: list[HashResult] = []
        self._task_id: str | None = None

        self.file_list = QListWidget(self)
        self.file_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        add_files_button = QPushButton("添加文件…", self)
        add_files_button.clicked.connect(self._add_files)
        add_folder_button = QPushButton("添加文件夹…", self)
        add_folder_button.clicked.connect(self._add_folder)
        clear_button = QPushButton("清空", self)
        clear_button.clicked.connect(self.file_list.clear)
        file_row = QHBoxLayout()
        for button in (add_files_button, add_folder_button, clear_button):
            file_row.addWidget(button)
        file_row.addStretch(1)

        self.algorithm_combo = QComboBox(self)
        for key, label in ALGORITHMS.items():
            self.algorithm_combo.addItem(label, key)
        index = self.algorithm_combo.findData(settings.settings.hash_algorithm)
        self.algorithm_combo.setCurrentIndex(max(index, 0))
        self.run_button = QPushButton("开始计算", self)
        self.run_button.clicked.connect(self._start)
        self.status_label = QLabel("尚未计算", self)
        self.status_label.setStyleSheet("color: #6b7280;")
        controls = QHBoxLayout()
        controls.addWidget(QLabel("算法：", self))
        controls.addWidget(self.algorithm_combo)
        controls.addWidget(self.run_button)
        controls.addWidget(self.status_label, 1)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["文件", "算法", "哈希值", "大小"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        self.copy_button = QPushButton("复制选中", self)
        self.copy_button.clicked.connect(self._copy_selected)
        self.copy_all_button = QPushButton("复制全部", self)
        self.copy_all_button.clicked.connect(self._copy_all)
        self.export_button = QPushButton("导出 TXT…", self)
        self.export_button.clicked.connect(self._export)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        for button in (self.copy_button, self.copy_all_button, self.export_button):
            buttons.addButton(button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.file_list, 1)
        layout.addLayout(file_row)
        layout.addLayout(controls)
        layout.addWidget(self.table, 2)
        layout.addWidget(buttons)

        for path in paths or []:
            self.file_list.addItem(str(path))
        self.manager.task_finished.connect(self._on_task_finished)
        self._update_buttons()

    # ------------------------------------------------------------
    def _add_files(self) -> None:
        archive_filter = "压缩包 (*" + " *".join(ArchiveFormat.all_suffixes()) + ");;所有文件 (*)"
        files, _ = QFileDialog.getOpenFileNames(self, "选择文件", "", archive_filter)
        for file_name in files:
            self.file_list.addItem(file_name)

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            self.file_list.addItem(folder)

    def paths(self) -> list[Path]:
        return [Path(self.file_list.item(index).text()) for index in range(self.file_list.count())]

    def _start(self) -> None:
        paths = self.paths()
        if not paths:
            QMessageBox.warning(self, "缺少文件", "请先添加要计算哈希的文件。")
            return
        algorithm = str(self.algorithm_combo.currentData())
        self.settings.update(hash_algorithm=algorithm)
        self.table.setRowCount(0)
        self.results = []
        self.status_label.setText("计算中…")
        self.run_button.setEnabled(False)
        task = HashTask(paths, algorithm=algorithm)
        task.signals.finished.connect(lambda _tid, _info: task.results and self._show_results(task.results))
        self._task_id = self.manager.submit(task)

    def _on_task_finished(self, task_id: str, info: object) -> None:
        if task_id != self._task_id:
            return
        status = getattr(info, "status", None)
        message = getattr(info, "message", "")
        self.run_button.setEnabled(True)
        self.status_label.setText(str(message))
        if status is TaskStatus.FAILED:
            QMessageBox.critical(self, "计算失败", str(message))
        self._task_id = None

    def _show_results(self, results: list[HashResult]) -> None:
        self.results = results
        self.table.setRowCount(len(results))
        for row, result in enumerate(results):
            self.table.setItem(row, 0, QTableWidgetItem(str(result.path)))
            self.table.setItem(row, 1, QTableWidgetItem(result.algorithm_label))
            self.table.setItem(row, 2, QTableWidgetItem(result.digest or (result.error or "")))
            self.table.setItem(row, 3, QTableWidgetItem(human_size(result.size)))
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_results = bool(self.results)
        self.copy_button.setEnabled(has_results)
        self.copy_all_button.setEnabled(has_results)
        self.export_button.setEnabled(has_results)

    def _copy_selected(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if not rows:
            return
        lines = [self.results[row].to_line() for row in rows if row < len(self.results)]
        QGuiApplication.clipboard().setText("\n".join(lines))

    def _copy_all(self) -> None:
        QGuiApplication.clipboard().setText(results_to_text(self.results))

    def _export(self) -> None:
        if not self.results:
            return
        default_name = self.results[0].path.with_suffix(".hashes.txt")
        target, _ = QFileDialog.getSaveFileName(self, "导出哈希结果", str(default_name), "文本文件 (*.txt)")
        if not target:
            return
        try:
            export_results(self.results, Path(target))
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", f"已导出到：\n{target}")

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.key() == Qt.Key.Key_Escape and self._task_id:
            self.manager.cancel(self._task_id)
            return
        super().keyPressEvent(event)
