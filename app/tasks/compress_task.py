"""压缩任务（含批量压缩与分卷）。"""

from __future__ import annotations

from pathlib import Path

from app.core.archive_manager import ArchiveManager
from app.core.options import CreateOptions
from app.core.utils import human_size
from app.tasks.base_task import BaseTask, TaskContext, TaskKind, TaskOutcome


class CompressTask(BaseTask):
    """创建压缩包（支持多来源与分卷）。"""

    kind = TaskKind.COMPRESS

    def __init__(self, manager: ArchiveManager, options: CreateOptions) -> None:
        super().__init__(
            title=f"压缩 → {options.target.name}",
            source="、".join(item.name for item in options.sources[:3]),
            target=str(options.target),
        )
        self.manager = manager
        self.options = options

    def execute(self, context: TaskContext) -> TaskOutcome:
        context.log(f"开始压缩 {len(self.options.sources)} 个项目")
        result = self.manager.create_archive(
            self.options,
            progress=context.progress,
            is_cancelled=context.is_cancelled,
        )
        size = result.target.stat().st_size if result.target.exists() else 0
        if result.volume_set is not None:
            size = result.volume_set.total_size
        detail = result.describe()
        return TaskOutcome(
            result_message=f"压缩完成（{human_size(size)}）",
            detail=detail,
            payload=result,
        )


class BatchCompressTask(BaseTask):
    """批量压缩：每个文件夹独立生成一个压缩包。"""

    kind = TaskKind.COMPRESS

    def __init__(
        self,
        manager: ArchiveManager,
        folders: list[Path],
        *,
        target_dir: Path,
        template: CreateOptions,
    ) -> None:
        super().__init__(
            title=f"批量压缩 {len(folders)} 个项目",
            source="、".join(item.name for item in folders[:3]),
            target=str(target_dir),
        )
        self.manager = manager
        self.folders = folders
        self.target_dir = target_dir
        self.template = template

    def execute(self, context: TaskContext) -> TaskOutcome:
        results: list[str] = []
        total = len(self.folders)
        for index, folder in enumerate(self.folders, start=1):
            context.check_cancelled()
            archive_format = self.template.resolved_format()
            target = self.target_dir / f"{folder.name}{archive_format.default_suffix}"
            options = CreateOptions(
                target=target,
                sources=[folder],
                format=archive_format,
                level=self.template.level,
                password=self.template.password,
                volume_size=self.template.volume_size,
                replace_existing=True,
                include_root=True,
            )
            context.set_message(f"[{index}/{total}] {folder.name}")
            result = self.manager.create_archive(
                options, progress=context.progress, is_cancelled=context.is_cancelled
            )
            results.append(result.describe())
        return TaskOutcome(
            result_message=f"批量压缩完成：{len(results)} 个压缩包",
            detail="\n".join(results),
            payload=results,
        )


class MergeCompressTask(BaseTask):
    """把多个来源合并压缩为一个压缩包。"""

    kind = TaskKind.COMPRESS

    def __init__(self, manager: ArchiveManager, options: CreateOptions) -> None:
        super().__init__(
            title=f"合并压缩 {len(options.sources)} 个项目",
            source="、".join(item.name for item in options.sources[:3]),
            target=str(options.target),
        )
        self.manager = manager
        self.options = options

    def execute(self, context: TaskContext) -> TaskOutcome:
        result = self.manager.create_archive(
            self.options,
            progress=context.progress,
            is_cancelled=context.is_cancelled,
        )
        return TaskOutcome(
            result_message="合并压缩完成",
            detail=result.describe(),
            payload=result,
        )
