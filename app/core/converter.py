"""压缩格式转换：读取源压缩包 → 解压到临时目录 → 重新压缩为目标格式。"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from app.core.archive_manager import ArchiveManager, CreateResult
from app.core.engine_base import (
    CancelCheck,
    ProgressCallback,
    never_cancelled,
    noop_progress,
)
from app.core.errors import PackPilotError, TaskCancelledError
from app.core.options import ConvertOptions, CreateOptions, ExtractOptions, OverwritePolicy
from app.core.utils import remove_tree

logger = logging.getLogger(__name__)


class ArchiveConverter:
    """把一种压缩格式转换为另一种格式，失败时不会破坏源文件。"""

    def __init__(self, manager: ArchiveManager) -> None:
        self.manager = manager

    def convert(
        self,
        options: ConvertOptions,
        *,
        progress: ProgressCallback = noop_progress,
        is_cancelled: CancelCheck = never_cancelled,
    ) -> CreateResult:
        options.source = Path(options.source)
        options.target = Path(options.target)
        if options.source.resolve() == options.target.resolve():
            raise PackPilotError("源文件与目标文件不能相同")
        source_info = self.manager.list_archive(options.source, password=options.source_password)
        logger.info(
            "开始转换：%s（%d 个文件，%d 字节）→ %s",
            options.source.name,
            source_info.file_count,
            source_info.total_size,
            options.target.name,
        )
        target_existed = options.target.exists()

        def phase_progress(base: int, span: int) -> ProgressCallback:
            def callback(done: int, total: int, message: str) -> None:
                if total <= 0:
                    progress(base, 100, message)
                    return
                progress(base + int(span * done / total), 100, message)

            return callback

        with tempfile.TemporaryDirectory(prefix="packpilot-convert-") as workspace_name:
            workspace = Path(workspace_name)
            extract_options = ExtractOptions(
                target=workspace,
                entries=options.entries,
                password=options.source_password,
                policy=OverwritePolicy.OVERWRITE,
                check_space=False,
            )
            self.manager.extract_archive(
                options.source,
                extract_options,
                progress=phase_progress(0, 50),
                is_cancelled=is_cancelled,
            )
            if is_cancelled():
                raise TaskCancelledError("转换任务已取消")

            create_options = CreateOptions(
                target=options.target,
                sources=[workspace],
                format=options.resolved_format(),
                level=options.level,
                password=options.target_password,
                volume_size=options.volume_size,
                replace_existing=True,
                include_root=False,
            )
            try:
                result = self.manager.create_archive(
                    create_options,
                    progress=phase_progress(50, 50),
                    is_cancelled=is_cancelled,
                )
            except Exception:
                self._cleanup_failed_target(options.target, existed=target_existed)
                raise
            progress(100, 100, "转换完成")
            return result

    @staticmethod
    def _cleanup_failed_target(target: Path, *, existed: bool) -> None:
        """转换失败时清理未完成的输出文件，源文件始终保留。"""

        if existed:
            return
        try:
            if target.is_dir():
                remove_tree(target)
            else:
                target.unlink(missing_ok=True)
            for leftover in target.parent.glob(target.name + ".0*"):
                leftover.unlink(missing_ok=True)
            manifest = target.with_name(target.name + ".packpilot-parts.json")
            manifest.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - 清理失败不影响主流程
            logger.warning("清理未完成的转换输出失败：%s", target)
