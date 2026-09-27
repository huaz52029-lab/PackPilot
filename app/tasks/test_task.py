"""压缩包测试任务。"""

from __future__ import annotations

from pathlib import Path

from app.core.archive_manager import ArchiveManager
from app.tasks.base_task import BaseTask, TaskContext, TaskKind, TaskOutcome


class TestTask(BaseTask):
    """完整读取并校验压缩包。"""

    #: 避免被 pytest 误判为测试类
    __test__ = False

    kind = TaskKind.TEST

    def __init__(self, manager: ArchiveManager, archive: Path, *, password: str | None = None) -> None:
        super().__init__(title=f"测试 {archive.name}", source=str(archive))
        self.manager = manager
        self.archive = archive
        self.password = password

    def execute(self, context: TaskContext) -> TaskOutcome:
        report = self.manager.test_archive(
            self.archive,
            password=self.password,
            progress=context.progress,
            is_cancelled=context.is_cancelled,
        )
        detail_lines = list(report.notes)
        if report.failures:
            detail_lines.append("失败条目：")
            detail_lines.extend(f"  - {name}" for name in report.failures[:50])
        return TaskOutcome(
            result_message=report.summary(),
            detail="\n".join(detail_lines),
            payload=report,
        )
