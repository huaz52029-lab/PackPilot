"""智能解压：自动判断合理的默认解压目录，避免重复嵌套或污染当前目录。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.archive_info import ArchiveFormat, ArchiveInfo, strip_volume_suffix
from app.core.utils import sanitize_filename


@dataclass(slots=True)
class SmartExtractPlan:
    """智能解压决策结果。"""

    directory: Path
    reason: str
    uses_archive_stem: bool


def archive_stem(path: Path) -> str:
    """去掉全部受支持扩展名后的压缩包名（``a.tar.gz`` → ``a``）。"""

    name = strip_volume_suffix(Path(path).name)
    lowered = name.lower()
    ordered = sorted(
        ArchiveFormat, key=lambda item: max(len(suffix) for suffix in item.suffixes), reverse=True
    )
    for archive_format in ordered:
        for suffix in archive_format.suffixes:
            if lowered.endswith(suffix):
                return name[: len(name) - len(suffix)] or "archive"
    return Path(name).stem or "archive"


def plan_smart_extract(archive_path: Path, info: ArchiveInfo) -> SmartExtractPlan:
    """根据压缩包结构决定默认解压目录。

    规则：

    1. 压缩包内已存在唯一顶层目录 → 直接解压到当前目录，避免 ``test/test/a.txt``；
    2. 仅包含一个顶层文件 → 解压到当前目录，避免为了单个文件新建目录；
    3. 其它情况 → 解压到以压缩包名命名的子目录，避免污染当前目录。
    """

    archive_path = Path(archive_path)
    parent = archive_path.parent if str(archive_path.parent) else Path.cwd()
    stem = sanitize_filename(archive_stem(archive_path))
    common_root = info.common_root()
    if common_root:
        return SmartExtractPlan(
            directory=parent,
            reason=f"压缩包内已包含顶层目录 “{common_root}/”，直接解压到当前目录可避免重复嵌套",
            uses_archive_stem=False,
        )

    tops = info.top_level_names()
    if info.file_count == 1 and len(tops) == 1 and not any(entry.is_dir for entry in info.entries):
        return SmartExtractPlan(
            directory=parent,
            reason="压缩包仅包含一个文件，直接解压到当前目录",
            uses_archive_stem=False,
        )
    if not info.entries:
        return SmartExtractPlan(
            directory=parent / stem,
            reason="压缩包为空，解压到以压缩包名命名的目录",
            uses_archive_stem=True,
        )
    return SmartExtractPlan(
        directory=parent / stem,
        reason=f"压缩包包含 {len(tops)} 个顶层项目，解压到 “{stem}/” 以避免污染当前目录",
        uses_archive_stem=True,
    )


def suggested_extract_directory(archive_path: Path, info: ArchiveInfo, *, smart: bool = True) -> Path:
    """返回默认解压目录：智能模式使用 :func:`plan_smart_extract`，否则使用压缩包同名目录。"""

    archive_path = Path(archive_path)
    if smart:
        return plan_smart_extract(archive_path, info).directory
    return archive_path.parent / sanitize_filename(archive_stem(archive_path))
