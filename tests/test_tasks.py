"""任务中心测试：状态流转、进度、速度、取消、失败传播与历史记录。"""

from __future__ import annotations

import time
from pathlib import Path

from app.core.archive_manager import ArchiveManager
from app.core.errors import PackPilotError
from app.core.options import CompressionLevel, CreateOptions, ExtractOptions, OverwritePolicy
from app.services.history import HistoryService
from app.tasks.base_task import BaseTask, TaskContext, TaskKind, TaskOutcome, TaskStatus
from app.tasks.compress_task import CompressTask
from app.tasks.convert_task import ConvertTask
from app.tasks.extract_task import ExtractTask, OpenEntryTask
from app.tasks.hash_task import HashTask
from app.tasks.scan_task import ScanTask
from app.tasks.task_manager import TaskManager
from app.tasks.test_task import TestTask


class LoopTask(BaseTask):
    """可取消的循环任务（用于确定性测试取消流程）。"""

    kind = TaskKind.SCAN

    def __init__(self, steps: int = 5000) -> None:
        super().__init__(title="循环任务")
        self.steps = steps
        self.completed_steps = 0

    def execute(self, context: TaskContext) -> TaskOutcome:
        for index in range(self.steps):
            context.check_cancelled()
            time.sleep(0.001)
            self.completed_steps = index + 1
            context.progress(index + 1, self.steps, f"步骤 {index + 1}")
        return TaskOutcome(result_message="循环完成")


class FailingTask(BaseTask):
    kind = TaskKind.SCAN

    def __init__(self) -> None:
        super().__init__(title="失败任务")

    def execute(self, context: TaskContext) -> TaskOutcome:
        raise PackPilotError("模拟的业务失败")


class CrashTask(BaseTask):
    kind = TaskKind.SCAN

    def __init__(self) -> None:
        super().__init__(title="异常任务")

    def execute(self, context: TaskContext) -> TaskOutcome:
        raise RuntimeError("未预期异常")


def _wait(task_manager: TaskManager, task_id: str, qapp, timeout: float = 30.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        info = task_manager.info(task_id)
        if info is not None and info.status.is_final:
            qapp.processEvents()
            return
        time.sleep(0.01)
    raise AssertionError("任务未在超时时间内结束")


def test_task_lifecycle_and_progress(qapp) -> None:  # type: ignore[no-untyped-def]
    manager = TaskManager(max_threads=2)
    updates: list[int] = []
    manager.task_updated.connect(lambda _task_id, info: updates.append(info.percent))
    task_id = manager.submit(LoopTask(steps=50))
    _wait(manager, task_id, qapp)
    info = manager.info(task_id)
    assert info is not None
    assert info.status is TaskStatus.COMPLETED
    assert info.percent == 100
    assert updates, "应产生进度更新"


def test_task_cancel(qapp) -> None:  # type: ignore[no-untyped-def]
    manager = TaskManager(max_threads=1)
    task = LoopTask(steps=100_000)
    task_id = manager.submit(task)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and task.completed_steps < 3:
        qapp.processEvents()
        time.sleep(0.01)
    assert manager.cancel(task_id) is True
    _wait(manager, task_id, qapp)
    info = manager.info(task_id)
    assert info is not None
    assert info.status is TaskStatus.CANCELLED
    assert task.completed_steps < task.steps


def test_task_failure_records_error(qapp) -> None:  # type: ignore[no-untyped-def]
    manager = TaskManager(max_threads=1)
    task_id = manager.submit(FailingTask())
    _wait(manager, task_id, qapp)
    info = manager.info(task_id)
    assert info is not None
    assert info.status is TaskStatus.FAILED
    assert "模拟的业务失败" in info.error


def test_task_unexpected_exception_is_captured(qapp) -> None:  # type: ignore[no-untyped-def]
    manager = TaskManager(max_threads=1)
    task_id = manager.submit(CrashTask())
    _wait(manager, task_id, qapp)
    info = manager.info(task_id)
    assert info is not None
    assert info.status is TaskStatus.FAILED
    assert "RuntimeError" in info.error


def test_cancel_latest_and_clear_finished(qapp) -> None:  # type: ignore[no-untyped-def]
    manager = TaskManager(max_threads=1)
    task_id = manager.submit(LoopTask(steps=50))
    _wait(manager, task_id, qapp)
    assert manager.active_count() == 0
    assert manager.clear_finished() == 1
    assert manager.all_tasks() == []
    assert manager.cancel_latest() is False


def test_real_compress_extract_test_tasks(qapp, tmp_path: Path, sample_tree: Path) -> None:  # type: ignore[no-untyped-def]
    from app.core.archive_info import ArchiveFormat

    archive_manager = ArchiveManager()
    task_manager = TaskManager(max_threads=2)
    archive = tmp_path / "任务.zip"
    compress_id = task_manager.submit(
        CompressTask(
            archive_manager,
            CreateOptions(
                target=archive,
                sources=[sample_tree],
                format=ArchiveFormat.ZIP,
                level=CompressionLevel.FASTEST,
            ),
        )
    )
    _wait(task_manager, compress_id, qapp)
    assert archive.exists()
    assert task_manager.info(compress_id).status is TaskStatus.COMPLETED

    test_id = task_manager.submit(TestTask(archive_manager, archive))
    _wait(task_manager, test_id, qapp)
    info = task_manager.info(test_id)
    assert info is not None and info.status is TaskStatus.COMPLETED
    assert "通过" in info.message

    extract_id = task_manager.submit(
        ExtractTask(
            archive_manager,
            archive,
            ExtractOptions(target=tmp_path / "解压", policy=OverwritePolicy.RENAME),
        )
    )
    _wait(task_manager, extract_id, qapp)
    assert (tmp_path / "解压" / "测试项目" / "中文 文件夹" / "测试文件.txt").exists()


def test_hash_convert_scan_open_entry_tasks(qapp, tmp_path: Path, sample_tree: Path) -> None:  # type: ignore[no-untyped-def]
    from app.core.options import ConvertOptions

    archive_manager = ArchiveManager()
    task_manager = TaskManager(max_threads=2)
    archive = tmp_path / "任务.zip"
    archive_manager.create_archive(CreateOptions(target=archive, sources=[sample_tree]))

    hash_id = task_manager.submit(HashTask([archive], algorithm="sha256"))
    _wait(task_manager, hash_id, qapp)
    hash_task = task_manager.task(hash_id)
    assert hash_task is not None
    assert hash_task.results and hash_task.results[0].digest

    scan_id = task_manager.submit(ScanTask(archive_manager, archive))
    _wait(task_manager, scan_id, qapp)
    assert task_manager.info(scan_id).status is TaskStatus.COMPLETED

    convert_id = task_manager.submit(
        ConvertTask(
            archive_manager,
            ConvertOptions(source=archive, target=tmp_path / "转换.7z", level=CompressionLevel.FASTEST),
        )
    )
    _wait(task_manager, convert_id, qapp)
    assert (tmp_path / "转换.7z").exists()
    assert task_manager.info(convert_id).status is TaskStatus.COMPLETED

    open_id = task_manager.submit(
        OpenEntryTask(
            archive_manager,
            archive,
            "测试项目/中文 文件夹/测试文件.txt",
            tmp_path / "临时",
        )
    )
    _wait(task_manager, open_id, qapp)
    open_task = task_manager.task(open_id)
    assert open_task is not None
    assert open_task.extracted_path is not None and open_task.extracted_path.exists()


def test_history_written_on_finish(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    history = HistoryService(tmp_path / "history.json")
    manager = TaskManager(max_threads=1, history=history)
    task_id = manager.submit(LoopTask(steps=10))
    _wait(manager, task_id, qapp)
    records = history.records()
    assert len(records) == 1
    assert records[0].status == TaskStatus.COMPLETED.label
    assert records[0].duration >= 0


def test_progress_math(qapp) -> None:  # type: ignore[no-untyped-def]
    task = LoopTask(steps=10)
    task._started_monotonic = time.monotonic() - 1.0
    task.report_progress(5, 10, "一半")
    info = task.snapshot()
    assert info.percent == 50
    assert info.speed_bps > 0
    assert info.eta_seconds is not None and info.eta_seconds > 0
