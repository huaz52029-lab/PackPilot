"""解压任务（含批量解压）。"""

from __future__ import annotations

from pathlib import Path

from app.core.archive_manager import ArchiveManager
from app.core.options import ExtractOptions
from app.tasks.base_task import BaseTask, TaskContext, TaskKind, TaskOutcome


class ExtractTask(BaseTask):
    """解压单个压缩包（支持选中条目）。"""

    kind = TaskKind.EXTRACT

    def __init__(self, manager: ArchiveManager, archive: Path, options: ExtractOptions) -> None:
        selection = f"（{len(options.entries)} 个条目）" if options.entries else "（全部）"
        super().__init__(
            title=f"解压 {archive.name} {selection}",
            source=str(archive),
            target=str(options.target),
        )
        self.manager = manager
        self.archive = archive
        self.options = options

    def execute(self, context: TaskContext) -> TaskOutcome:
        context.log(f"解压到 {self.options.target}")
        result = self.manager.extract_archive(
            self.archive,
            self.options,
            progress=context.progress,
            is_cancelled=context.is_cancelled,
        )
        warnings = "\n".join(result.warnings[:10])
        return TaskOutcome(
            result_message=result.summary(),
            detail=f"目标目录：{result.target}\n{warnings}".strip(),
            payload=result,
        )


class BatchExtractTask(BaseTask):
    """批量解压：统一解压到同一个目标目录并逐个汇报进度。"""

    kind = TaskKind.EXTRACT

    def __init__(
        self,
        manager: ArchiveManager,
        archives: list[Path],
        *,
        target_dir: Path,
        template: ExtractOptions,
        use_stem_subdir: bool = True,
    ) -> None:
        super().__init__(
            title=f"批量解压 {len(archives)} 个压缩包",
            source="、".join(item.name for item in archives[:3]),
            target=str(target_dir),
        )
        self.manager = manager
        self.archives = archives
        self.target_dir = target_dir
        self.template = template
        self.use_stem_subdir = use_stem_subdir

    def execute(self, context: TaskContext) -> TaskOutcome:
        from app.core.smart_extract import archive_stem

        total = len(self.archives)
        results: list[str] = []
        for index, archive in enumerate(self.archives, start=1):
            context.check_cancelled()
            target = self.target_dir
            if self.use_stem_subdir:
                target = self.target_dir / archive_stem(archive)
            options = ExtractOptions(
                target=target,
                password=self.template.password,
                policy=self.template.policy,
                limits=self.template.limits,
                check_space=self.template.check_space,
            )
            context.set_message(f"[{index}/{total}] {archive.name}")
            result = self.manager.extract_archive(
                archive, options, progress=context.progress, is_cancelled=context.is_cancelled
            )
            results.append(f"{archive.name} → {result.summary()}")
        return TaskOutcome(
            result_message=f"批量解压完成：{len(results)} 个压缩包",
            detail="\n".join(results),
            payload=results,
        )


class OpenEntryTask(BaseTask):
    """把压缩包内单个文件解压到临时目录，供默认程序打开。"""

    kind = TaskKind.EXTRACT

    def __init__(
        self,
        manager: ArchiveManager,
        archive: Path,
        entry_name: str,
        workspace: Path,
        *,
        password: str | None = None,
    ) -> None:
        super().__init__(
            title=f"准备打开 {Path(entry_name).name}",
            source=str(archive),
            target=str(workspace),
        )
        self.manager = manager
        self.archive = Path(archive)
        self.entry_name = entry_name
        self.workspace = Path(workspace)
        self.password = password
        self.extracted_path: Path | None = None

    def execute(self, context: TaskContext) -> TaskOutcome:
        context.log(f"临时解压 {self.entry_name}")
        target = self.manager.extract_entry_to_temp(
            self.archive,
            self.entry_name,
            self.workspace,
            password=self.password,
            progress=context.progress,
            is_cancelled=context.is_cancelled,
        )
        self.extracted_path = target
        return TaskOutcome(
            result_message=f"已临时解压 {target.name}",
            detail=f"临时文件：{target}",
            payload=target,
        )
