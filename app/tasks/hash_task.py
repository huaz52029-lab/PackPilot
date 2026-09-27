"""哈希计算任务。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from app.core.checksum import ALGORITHMS, HashResult, hash_files, normalize_algorithm
from app.tasks.base_task import BaseTask, TaskContext, TaskKind, TaskOutcome


class HashTask(BaseTask):
    """计算一个或多个文件的哈希值。"""

    kind = TaskKind.HASH

    def __init__(self, paths: Sequence[Path], *, algorithm: str = "sha256") -> None:
        key = normalize_algorithm(algorithm)
        names = "、".join(path.name for path in paths[:3])
        super().__init__(
            title=f"计算 {ALGORITHMS[key]}（{len(paths)} 个文件）",
            source=names,
        )
        self.paths = [Path(path) for path in paths]
        self.algorithm = key
        self.results: list[HashResult] = []

    def execute(self, context: TaskContext) -> TaskOutcome:
        results = hash_files(
            self.paths,
            self.algorithm,
            progress=context.progress,
            is_cancelled=context.is_cancelled,
        )
        self.results = results
        failed = [result for result in results if not result.ok]
        detail = "\n".join(f"{result.path.name}\t{result.digest or result.error}" for result in results[:200])
        if failed:
            return TaskOutcome(
                result_message=f"完成，{len(failed)} 个文件读取失败",
                detail=detail,
                payload=results,
            )
        return TaskOutcome(
            result_message=f"哈希计算完成（{ALGORITHMS[self.algorithm]}）",
            detail=detail,
            payload=results,
        )
