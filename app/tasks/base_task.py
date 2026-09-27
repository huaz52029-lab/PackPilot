"""后台任务基类：进度、速度、剩余时间、取消与状态机。"""

from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from abc import abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum

from PySide6.QtCore import QObject, QRunnable, Signal

from app.core.errors import PackPilotError, TaskCancelledError

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """任务状态（界面中文显示）。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {
            TaskStatus.PENDING: "等待",
            TaskStatus.RUNNING: "运行中",
            TaskStatus.COMPLETED: "完成",
            TaskStatus.FAILED: "失败",
            TaskStatus.CANCELLED: "取消",
        }[self]

    @property
    def is_final(self) -> bool:
        return self in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


class TaskKind(str, Enum):
    """任务类型。"""

    COMPRESS = "compress"
    EXTRACT = "extract"
    TEST = "test"
    CONVERT = "convert"
    HASH = "hash"
    SCAN = "scan"

    @property
    def label(self) -> str:
        return {
            TaskKind.COMPRESS: "压缩",
            TaskKind.EXTRACT: "解压",
            TaskKind.TEST: "测试",
            TaskKind.CONVERT: "转换",
            TaskKind.HASH: "哈希",
            TaskKind.SCAN: "扫描",
        }[self]


@dataclass(slots=True)
class TaskInfo:
    """任务中心的展示数据。"""

    id: str
    kind: TaskKind
    title: str
    status: TaskStatus = TaskStatus.PENDING
    percent: int = 0
    message: str = ""
    current_file: str = ""
    done_items: int = 0
    total_items: int = 0
    speed_bps: float = 0.0
    elapsed: float = 0.0
    eta_seconds: float | None = None
    source: str = ""
    target: str = ""
    error: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def status_label(self) -> str:
        return self.status.label

    @property
    def is_active(self) -> bool:
        return self.status in {TaskStatus.PENDING, TaskStatus.RUNNING}


class TaskSignals(QObject):
    """任务信号（跨线程发送到 GUI 线程）。"""

    started = Signal(str)
    progress = Signal(str, object)
    message = Signal(str, str)
    finished = Signal(str, object)


class TaskContext:
    """任务执行期间使用的进度与取消上下文。"""

    def __init__(self, task: BaseTask) -> None:
        self._task = task

    def progress(self, done: int, total: int, message: str = "") -> None:
        self._task.report_progress(done, total, message)

    def log(self, message: str) -> None:
        self._task.signals.message.emit(self._task.id, message)
        logger.info("[%s] %s", self._task.title, message)

    def is_cancelled(self) -> bool:
        return self._task.is_cancelled()

    def check_cancelled(self) -> None:
        if self._task.is_cancelled():
            raise TaskCancelledError("任务已取消")

    def set_message(self, message: str) -> None:
        self._task.set_message(message)


class BaseTask(QRunnable):
    """所有后台任务的基类。"""

    kind: TaskKind = TaskKind.SCAN

    def __init__(self, title: str, *, source: str = "", target: str = "") -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.id = uuid.uuid4().hex
        self.title = title
        self.signals = TaskSignals()
        self.info = TaskInfo(
            id=self.id,
            kind=self.kind,
            title=title,
            source=source,
            target=target,
            message="等待开始",
        )
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._started_monotonic = 0.0
        self._last_emit = 0.0
        self._result: object = None

    # ------------------------------------------------------------ 控制
    def cancel(self) -> None:
        self._cancel_event.set()
        if self.info.status is TaskStatus.PENDING:
            self.info.status = TaskStatus.CANCELLED
            self.info.message = "已取消（尚未开始）"

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @property
    def result(self) -> object:
        return self._result

    # ------------------------------------------------------------ 进度
    def report_progress(self, done: int, total: int, message: str = "") -> None:
        now = time.monotonic()
        percent = int(done * 100 / total) if total > 0 else 0
        percent = max(0, min(100, percent))
        with self._lock:
            self.info.done_items = done
            self.info.total_items = total
            self.info.percent = percent
            if message:
                self.info.current_file = message
                self.info.message = message
            elapsed = now - self._started_monotonic if self._started_monotonic else 0.0
            self.info.elapsed = elapsed
            if elapsed > 0.2 and done > 0:
                self.info.speed_bps = done / elapsed
                if percent > 0:
                    remaining = elapsed * (100 - percent) / percent
                    self.info.eta_seconds = remaining
        if now - self._last_emit >= 0.05 or percent >= 100:
            self._last_emit = now
            self.signals.progress.emit(self.id, self.snapshot())

    def set_message(self, message: str) -> None:
        with self._lock:
            self.info.message = message
        self.signals.progress.emit(self.id, self.snapshot())

    def snapshot(self) -> TaskInfo:
        with self._lock:
            return replace(self.info)

    # ------------------------------------------------------------ 执行
    def run(self) -> None:  # QRunnable 入口
        self.info.status = TaskStatus.RUNNING
        self.info.started_at = datetime.now()
        self.info.message = "运行中"
        self._started_monotonic = time.monotonic()
        self.signals.started.emit(self.id)
        context = TaskContext(self)
        try:
            if self.is_cancelled():
                raise TaskCancelledError("任务已取消")
            detail = self.execute(context)
            self._result = detail
            if self.is_cancelled():
                raise TaskCancelledError("任务已取消")
            self.info.status = TaskStatus.COMPLETED
            self.info.percent = 100
            self.info.message = detail.result_message if isinstance(detail, TaskOutcome) else "完成"
            self.info.error = ""
        except TaskCancelledError as exc:
            self.info.status = TaskStatus.CANCELLED
            self.info.message = str(exc) or "已取消"
            self.info.error = ""
            logger.info("任务已取消：%s", self.title)
        except PackPilotError as exc:
            self.info.status = TaskStatus.FAILED
            self.info.message = str(exc)
            self.info.error = str(exc)
            logger.error("任务失败：%s（%s）", self.title, exc)
        except Exception as exc:
            self.info.status = TaskStatus.FAILED
            self.info.message = f"未预期的错误：{exc}"
            self.info.error = f"{exc.__class__.__name__}: {exc}"
            logger.critical("任务出现未预期异常：%s\n%s", self.title, traceback.format_exc())
        finally:
            self.info.finished_at = datetime.now()
            self.info.elapsed = time.monotonic() - self._started_monotonic
            self.info.eta_seconds = 0.0
            if self.info.status is TaskStatus.RUNNING:
                self.info.status = TaskStatus.FAILED
            self.signals.finished.emit(self.id, self.snapshot())

    @abstractmethod
    def execute(self, context: TaskContext) -> object:
        """在后台线程中执行任务主体。"""


@dataclass(slots=True)
class TaskOutcome:
    """任务结果摘要。"""

    result_message: str
    detail: str = ""
    payload: object = None
