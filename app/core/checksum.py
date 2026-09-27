"""文件哈希计算（MD5 / SHA-1 / SHA-256 / SHA-512）。"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import PackPilotError, TaskCancelledError
from app.core.utils import human_size, now_string

ALGORITHMS: dict[str, str] = {
    "md5": "MD5",
    "sha1": "SHA-1",
    "sha256": "SHA-256",
    "sha512": "SHA-512",
}
DEFAULT_ALGORITHM = "sha256"
CHUNK_SIZE = 1024 * 1024


@dataclass(slots=True)
class HashResult:
    """单个文件/压缩包的哈希结果。"""

    path: Path
    algorithm: str
    digest: str
    size: int = 0
    elapsed: float = 0.0
    error: str | None = None

    @property
    def algorithm_label(self) -> str:
        return ALGORITHMS.get(self.algorithm, self.algorithm.upper())

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_line(self) -> str:
        if self.error:
            return f"{self.path}\t{self.algorithm_label}\t错误：{self.error}"
        return f"{self.path}\t{self.algorithm_label}\t{self.digest}\t{human_size(self.size)}"


def normalize_algorithm(name: str) -> str:
    key = name.lower().replace("-", "")
    mapping = {"md5": "md5", "sha1": "sha1", "sha256": "sha256", "sha512": "sha512"}
    if key not in mapping:
        raise PackPilotError(f"不支持的哈希算法：{name}（可用：MD5、SHA-1、SHA-256、SHA-512）")
    return mapping[key]


def hash_file(
    path: Path,
    algorithm: str = DEFAULT_ALGORITHM,
    *,
    progress: object = None,
    is_cancelled: object = None,
) -> HashResult:
    """计算单个文件的哈希值。"""

    path = Path(path)
    key = normalize_algorithm(algorithm)
    if not path.exists():
        raise PackPilotError(f"文件不存在：{path}")
    if path.is_dir():
        raise PackPilotError(f"{path} 是目录，无法计算文件哈希")
    digest = hashlib.new(key)
    total = path.stat().st_size
    done = 0
    started = time.monotonic()
    try:
        with path.open("rb") as handle:
            while True:
                if callable(is_cancelled) and is_cancelled():
                    raise TaskCancelledError("哈希计算已取消")
                chunk = handle.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                done += len(chunk)
                if callable(progress):
                    progress(done, total, path.name)
    except OSError as exc:
        return HashResult(path=path, algorithm=key, digest="", size=total, error=str(exc))
    return HashResult(
        path=path,
        algorithm=key,
        digest=digest.hexdigest(),
        size=total,
        elapsed=time.monotonic() - started,
    )


def hash_files(
    paths: Iterable[Path],
    algorithm: str = DEFAULT_ALGORITHM,
    *,
    progress: object = None,
    is_cancelled: object = None,
) -> list[HashResult]:
    """批量计算哈希，逐个文件汇报进度。"""

    path_list = [Path(path) for path in paths]
    results: list[HashResult] = []
    total = len(path_list)
    for index, path in enumerate(path_list, start=1):
        if callable(is_cancelled) and is_cancelled():
            raise TaskCancelledError("哈希计算已取消")
        result = hash_file(path, algorithm, is_cancelled=is_cancelled)
        results.append(result)
        if callable(progress):
            progress(index, total, path.name)
    return results


def results_to_text(results: Sequence[HashResult], *, source: str = "") -> str:
    """把哈希结果格式化为可导出/复制的文本。"""

    from app.version import VERSION_DISPLAY

    lines = [
        f"# {VERSION_DISPLAY} 哈希校验结果",
        f"# 生成时间：{now_string()}",
    ]
    if source:
        lines.append(f"# 来源：{source}")
    lines.append("# 文件\t算法\t哈希值\t大小")
    lines.extend(result.to_line() for result in results)
    return "\n".join(lines) + "\n"


def export_results(results: Sequence[HashResult], target: Path, *, source: str = "") -> Path:
    """把哈希结果导出为 TXT 文件。"""

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(results_to_text(results, source=source), encoding="utf-8", newline="\n")
    return target


def compare_digest(digest: str, expected: str) -> bool:
    """恒定时间比较两个十六进制摘要（忽略大小写与空白）。"""

    return digest.strip().lower() == expected.strip().lower()
