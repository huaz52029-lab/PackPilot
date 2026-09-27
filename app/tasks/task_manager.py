"""任务中心：QThreadPool 调度、状态跟踪、取消与历史写入。"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from PySide6.QtCore import QObject, QThreadPool, Signal

from app.core.utils import now_string
from app.services.history import HistoryRecord, HistoryService
from app.tasks.base_task import BaseTask, TaskInfo, TaskOutcome

logger = logging.getLogger(__name__)


class TaskManager(QObject):
    """统一管理所有耗时任务。"""

    task_added = Signal(str)
    task_updated = Signal(str, object)
    task_finished = Signal(str, object)
    tasks_changed = Signal()

    def __init__(
        self,
        *,
        max_threads: int = 2,
        history: HistoryService | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(1, max_threads))
        self._tasks: dict[str, BaseTask] = {}
        self._order: list[str] = []
        self._history = history

    def submit(self, task: BaseTask) -> str:
        """把任务加入线程池并返回任务 ID。"""

        self._tasks[task.id] = task
        self._order.append(task.id)
        task.signals.started.connect(self._on_started)
        task.signals.progress.connect(self._on_progress)
        task.signals.message.connect(self._on_message)
        task.signals.finished.connect(self._on_finished)
        self.task_added.emit(task.id)
        self.tasks_changed.emit()
        logger.info("提交任务：%s（%s）", task.title, task.kind.label)
        self._pool.start(task)
        return task.id

    def task(self, task_id: str) -> BaseTask | None:
        return self._tasks.get(task_id)

    def info(self, task_id: str) -> TaskInfo | None:
        task = self._tasks.get(task_id)
        return task.snapshot() if task is not None else None

    def all_tasks(self) -> list[TaskInfo]:
        return [self._tasks[task_id].snapshot() for task_id in self._order if task_id in self._tasks]

    def active_tasks(self) -> list[TaskInfo]:
        return [info for info in self.all_tasks() if info.is_active]

    def active_count(self) -> int:
        return len(self.active_tasks())

    def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.info.status.is_final:
            return False
        task.cancel()
        self._emit_update(task)
        logger.info("请求取消任务：%s", task.title)
        return True

    def cancel_all(self) -> int:
        return sum(1 for info in self.active_tasks() if self.cancel(info.id))

    def cancel_latest(self) -> bool:
        """取消最近提交的活动任务（供 Esc 快捷键使用）。"""

        for task_id in reversed(self._order):
            task = self._tasks.get(task_id)
            if task is not None and task.info.is_active:
                return self.cancel(task_id)
        return False

    def wait_for_done(self, timeout_ms: int = 30_000) -> bool:
        return self._pool.waitForDone(timeout_ms)

    def clear_finished(self) -> int:
        finished = [info.id for info in self.all_tasks() if info.status.is_final]
        for task_id in finished:
            self._tasks.pop(task_id, None)
            if task_id in self._order:
                self._order.remove(task_id)
        if finished:
            self.tasks_changed.emit()
        return len(finished)

    def remove(self, task_id: str) -> bool:
        info = self.info(task_id)
        if info is None or not info.status.is_final:
            return False
        self._tasks.pop(task_id, None)
        if task_id in self._order:
            self._order.remove(task_id)
        self.tasks_changed.emit()
        return True

    def _on_started(self, task_id: str) -> None:
        self._emit_update(self._tasks.get(task_id))

    def _on_progress(self, task_id: str, _info: object) -> None:
        self._emit_update(self._tasks.get(task_id))

    def _on_message(self, task_id: str, message: str) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.info.message = message
        self._emit_update(task)

    def _on_finished(self, task_id: str, _info: object) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        snapshot = task.snapshot()
        self.task_finished.emit(task_id, snapshot)
        self.tasks_changed.emit()
        self._write_history(task)
        logger.info("任务结束：%s → %s", task.title, snapshot.status.label)

    def _emit_update(self, task: BaseTask | None) -> None:
        if task is None:
            return
        self.task_updated.emit(task.id, task.snapshot())

    def _write_history(self, task: BaseTask) -> None:
        if self._history is None:
            return
        snapshot = task.snapshot()
        detail = task.result.detail if isinstance(task.result, TaskOutcome) else ""
        try:
            self._history.add(
                HistoryRecord(
                    timestamp=now_string(),
                    kind=task.kind.label,
                    title=task.title,
                    source=snapshot.source,
                    target=snapshot.target,
                    status=snapshot.status.label,
                    duration=snapshot.elapsed,
                    error=snapshot.error,
                    detail=detail if isinstance(detail, str) else "",
                )
            )
        except Exception:
            logger.exception("写入任务历史失败")

    @property
    def pool(self) -> QThreadPool:
        return self._pool

    def task_ids(self) -> Iterable[str]:
        return tuple(self._order)
