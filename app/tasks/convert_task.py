"""格式转换任务。"""

from __future__ import annotations

from app.core.archive_manager import ArchiveManager
from app.core.converter import ArchiveConverter
from app.core.options import ConvertOptions
from app.tasks.base_task import BaseTask, TaskContext, TaskKind, TaskOutcome


class ConvertTask(BaseTask):
    """把压缩包转换为另一种格式。"""

    kind = TaskKind.CONVERT

    def __init__(self, manager: ArchiveManager, options: ConvertOptions) -> None:
        super().__init__(
            title=f"转换 {options.source.name} → {options.target.name}",
            source=str(options.source),
            target=str(options.target),
        )
        self.converter = ArchiveConverter(manager)
        self.options = options

    def execute(self, context: TaskContext) -> TaskOutcome:
        context.log(f"转换格式：{self.options.source.name} → {self.options.target.name}")
        result = self.converter.convert(
            self.options,
            progress=context.progress,
            is_cancelled=context.is_cancelled,
        )
        return TaskOutcome(
            result_message="转换完成",
            detail=result.describe(),
            payload=result,
        )
