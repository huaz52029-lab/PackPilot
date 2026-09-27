"""扫描任务：结构检查、安全检查与分卷完整性校验。"""

from __future__ import annotations

from pathlib import Path

from app.core.archive_manager import ArchiveManager
from app.core.archive_security import describe_issues
from app.tasks.base_task import BaseTask, TaskContext, TaskKind, TaskOutcome


class ScanTask(BaseTask):
    """检查压缩包结构与安全性；分卷压缩包会逐个校验分卷哈希。"""

    kind = TaskKind.SCAN

    def __init__(
        self,
        manager: ArchiveManager,
        archive: Path,
        *,
        password: str | None = None,
        verify_volumes: bool = True,
    ) -> None:
        super().__init__(title=f"扫描 {Path(archive).name}", source=str(archive))
        self.manager = manager
        self.archive = Path(archive)
        self.password = password
        self.verify_volumes = verify_volumes

    def execute(self, context: TaskContext) -> TaskOutcome:
        info = self.manager.list_archive(self.archive, password=self.password)
        issues = self.manager.security_scan(self.archive, password=self.password)
        lines = [
            f"格式：{info.format.display_name}",
            f"条目：{info.file_count} 个文件 / {info.dir_count} 个目录",
            f"解压后大小：{info.total_size} 字节",
            f"加密：{'是' if info.encrypted else '否'}",
            "",
            "安全检查：",
            describe_issues(issues),
        ]
        danger = any(issue.level.value == "danger" for issue in issues)
        volume_note = ""
        volume_set = self.manager.volume_set_for(self.archive)
        if volume_set is not None:
            context.set_message("校验分卷完整性")

            def volume_progress(done: int, total: int, message: str) -> None:
                context.progress(done, total, message)

            verified = self.manager.verify_volumes(self.archive)
            volume_note = verified.describe()
            lines.append("")
            lines.append("分卷检查：")
            lines.append(volume_note)
            del volume_progress
        summary = "扫描完成"
        if danger:
            summary = "扫描完成：发现安全风险"
        elif volume_set is not None:
            summary = "扫描完成：分卷完整" if not volume_set.problems else "扫描完成：分卷异常"
        return TaskOutcome(
            result_message=summary,
            detail="\n".join(lines),
            payload={"info": info, "issues": issues, "volumes": volume_note},
        )
