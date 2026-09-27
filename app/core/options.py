"""核心层共用的选项与结果数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from app.core.archive_info import ArchiveFormat


class CompressionLevel(str, Enum):
    """压缩等级（界面显示中文，底层映射到具体格式参数）。"""

    FASTEST = "fastest"
    FAST = "fast"
    NORMAL = "normal"
    HIGH = "high"
    ULTRA = "ultra"

    @property
    def label(self) -> str:
        return {
            CompressionLevel.FASTEST: "最快",
            CompressionLevel.FAST: "较快",
            CompressionLevel.NORMAL: "标准",
            CompressionLevel.HIGH: "较高",
            CompressionLevel.ULTRA: "最高",
        }[self]

    @property
    def description(self) -> str:
        return {
            CompressionLevel.FASTEST: "速度优先，压缩率最低",
            CompressionLevel.FAST: "偏重速度，压缩率较低",
            CompressionLevel.NORMAL: "速度与压缩率平衡（推荐）",
            CompressionLevel.HIGH: "压缩率优先，速度较慢",
            CompressionLevel.ULTRA: "追求最小体积，耗时最长",
        }[self]

    def zip_level(self) -> int:
        return {
            CompressionLevel.FASTEST: 1,
            CompressionLevel.FAST: 3,
            CompressionLevel.NORMAL: 6,
            CompressionLevel.HIGH: 8,
            CompressionLevel.ULTRA: 9,
        }[self]

    def gzip_level(self) -> int:
        return self.zip_level()

    def bz2_level(self) -> int:
        return self.zip_level()

    def xz_preset(self) -> int:
        return {
            CompressionLevel.FASTEST: 1,
            CompressionLevel.FAST: 3,
            CompressionLevel.NORMAL: 6,
            CompressionLevel.HIGH: 7,
            CompressionLevel.ULTRA: 9,
        }[self]

    def sevenzip_preset(self) -> int:
        return {
            CompressionLevel.FASTEST: 1,
            CompressionLevel.FAST: 3,
            CompressionLevel.NORMAL: 5,
            CompressionLevel.HIGH: 7,
            CompressionLevel.ULTRA: 9,
        }[self]

    @classmethod
    def from_label(cls, label: str) -> CompressionLevel:
        for level in cls:
            if level.label == label:
                return level
        return cls.NORMAL


class OverwritePolicy(str, Enum):
    """解压时的文件冲突处理策略。"""

    ASK = "ask"
    OVERWRITE = "overwrite"
    SKIP = "skip"
    RENAME = "rename"
    FAIL = "fail"

    @property
    def label(self) -> str:
        return {
            OverwritePolicy.ASK: "询问",
            OverwritePolicy.OVERWRITE: "覆盖",
            OverwritePolicy.SKIP: "跳过",
            OverwritePolicy.RENAME: "重命名冲突文件",
            OverwritePolicy.FAIL: "遇到冲突即停止",
        }[self]


@dataclass(slots=True)
class SafetyLimits:
    """安全阈值：用于解压炸弹与恶意压缩包防护。"""

    warn_compression_ratio: float = 100.0
    block_compression_ratio: float = 1000.0
    warn_total_size: int = 4 * 1024**3
    block_total_size: int = 64 * 1024**3
    max_entries: int = 200_000
    max_path_length: int = 32_000
    allow_symlinks: bool = False


@dataclass(slots=True)
class CreateOptions:
    """创建压缩包的参数。"""

    target: Path
    sources: list[Path]
    format: ArchiveFormat | None = None
    level: CompressionLevel = CompressionLevel.NORMAL
    password: str | None = None
    volume_size: int | None = None
    comment: str = ""
    replace_existing: bool = True
    include_root: bool = True

    def resolved_format(self) -> ArchiveFormat:
        if self.format is not None:
            return self.format
        detected = ArchiveFormat.from_path(self.target)
        if detected is None:
            raise ValueError(f"无法根据文件名判断压缩格式：{self.target.name}")
        return detected


@dataclass(slots=True)
class ExtractOptions:
    """解压参数。"""

    target: Path
    entries: list[str] | None = None
    password: str | None = None
    policy: OverwritePolicy = OverwritePolicy.ASK
    smart: bool = False
    limits: SafetyLimits = field(default_factory=SafetyLimits)
    check_space: bool = True
    #: True 表示 ``entries`` 是精确条目列表（不展开目录子项），供内部重写压缩包使用
    exact_entries: bool = False

    def normalized_entries(self) -> list[str] | None:
        if not self.entries:
            return None
        return list(dict.fromkeys(self.entries))


@dataclass(slots=True)
class ConvertOptions:
    """格式转换参数。"""

    source: Path
    target: Path
    level: CompressionLevel = CompressionLevel.NORMAL
    source_password: str | None = None
    target_password: str | None = None
    volume_size: int | None = None
    entries: list[str] | None = None

    def resolved_format(self) -> ArchiveFormat:
        detected = ArchiveFormat.from_path(self.target)
        if detected is None:
            raise ValueError(f"无法根据文件名判断目标格式：{self.target.name}")
        return detected


@dataclass(slots=True)
class TestReport:
    """压缩包测试结果。"""

    ok: bool
    checked: int
    total: int
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    elapsed: float = 0.0

    def summary(self) -> str:
        if self.ok:
            return f"测试通过：已检查 {self.checked}/{self.total} 个条目"
        first = self.failures[0] if self.failures else "未知错误"
        return f"测试失败：{len(self.failures)} 个条目损坏，首个失败条目：{first}"


@dataclass(slots=True)
class ExtractResult:
    """解压结果统计。"""

    target: Path
    extracted_files: int = 0
    extracted_directories: int = 0
    skipped: int = 0
    renamed: int = 0
    bytes_written: int = 0
    warnings: list[str] = field(default_factory=list)
    created_paths: list[Path] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"已解压 {self.extracted_files} 个文件"]
        if self.extracted_directories:
            parts.append(f"{self.extracted_directories} 个目录")
        if self.skipped:
            parts.append(f"跳过 {self.skipped} 个")
        if self.renamed:
            parts.append(f"重命名 {self.renamed} 个")
        return "，".join(parts)
