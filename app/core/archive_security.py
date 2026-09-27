"""压缩包安全：路径穿越防护、符号链接检查、磁盘空间与解压炸弹评估。"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath

from app.core.archive_info import ArchiveEntry, ArchiveInfo
from app.core.errors import InsufficientSpaceError, UnsafeArchiveError
from app.core.options import OverwritePolicy, SafetyLimits
from app.core.utils import ensure_directory, extended_length_path, human_size

_INVALID_NAME_CHARS = set('<>:"|?*')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CONIN$",
    "CONOUT$",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class IssueLevel(str, Enum):
    """安全问题级别。"""

    INFO = "info"
    WARNING = "warning"
    DANGER = "danger"

    @property
    def label(self) -> str:
        return {IssueLevel.INFO: "提示", IssueLevel.WARNING: "警告", IssueLevel.DANGER: "危险"}[self]


@dataclass(slots=True)
class SecurityIssue:
    """一条安全检查结论。"""

    level: IssueLevel
    kind: str
    message: str
    entries: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        prefix = f"[{self.level.label}] {self.message}"
        if self.entries:
            sample = "、".join(self.entries[:3])
            more = f" 等 {len(self.entries)} 项" if len(self.entries) > 3 else ""
            return f"{prefix}（{sample}{more}）"
        return prefix


@dataclass(slots=True)
class DiskSpaceReport:
    """磁盘空间检查结果。"""

    target: Path
    required: int
    available: int
    sufficient: bool

    def message(self) -> str:
        return f"需要空间：{human_size(self.required)}，当前剩余：{human_size(self.available)}"


def normalize_entry_name(name: str) -> str:
    """把条目名规范化为使用 ``/`` 的相对路径。"""

    normalized = name.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def entry_parts(name: str) -> list[str]:
    return [part for part in normalize_entry_name(name).split("/") if part not in ("", ".")]


def is_absolute_entry(name: str) -> bool:
    """判断条目名是否为绝对路径（含盘符、UNC、正反斜杠绝对路径）。"""

    raw = name.strip()
    if not raw:
        return False
    if raw.startswith(("/", "\\")):
        return True
    if PureWindowsPath(raw).is_absolute():
        return True
    if PurePosixPath(raw).is_absolute():
        return True
    # 形如 C:file.txt 的盘符相对路径同样不允许
    return bool(PureWindowsPath(raw).drive)


def validate_entry_name(name: str, limits: SafetyLimits | None = None) -> str | None:
    """检查条目名是否安全；返回问题描述，安全时返回 ``None``。"""

    limits = limits or SafetyLimits()
    if not name or not name.strip():
        return "条目名为空"
    if "\x00" in name:
        return "条目名包含空字符"
    if any(ord(char) < 32 for char in name):
        return "条目名包含控制字符"
    if len(name) > limits.max_path_length:
        return f"条目名超过最大长度（{len(name)} 字符）"
    if is_absolute_entry(name):
        return "条目名为绝对路径（含盘符或 UNC 路径）"

    for part in entry_parts(name):
        if part == "..":
            return "条目名包含上级目录引用（..）"
        if len(part) > 255:
            return "单级目录名超过 255 字符"
        if any(char in _INVALID_NAME_CHARS or ord(char) < 32 for char in part):
            return f"目录名包含 Windows 非法字符：{part}"
        if part.rstrip(" .") != part:
            return f"目录名以空格或点结尾：{part}"
        stem = part.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED_NAMES:
            return f"目录名为 Windows 保留设备名：{part}"
    return None


def is_safe_entry_name(name: str, limits: SafetyLimits | None = None) -> bool:
    return validate_entry_name(name, limits) is None


def safe_join(base_dir: Path, entry_name: str, limits: SafetyLimits | None = None) -> Path:
    """把条目名安全地拼接到目标目录，越界时抛出 :class:`UnsafeArchiveError`。"""

    problem = validate_entry_name(entry_name, limits)
    if problem:
        raise UnsafeArchiveError(
            f"检测到不安全的压缩包路径：{problem}",
            entries=[entry_name],
            detail=problem,
        )
    parts = entry_parts(entry_name)
    if not parts:
        raise UnsafeArchiveError("压缩包条目路径为空", entries=[entry_name])

    base_resolved = _resolve_for_compare(base_dir)
    candidate = base_dir
    for part in parts:
        candidate = candidate / part
    candidate_resolved = _resolve_for_compare(candidate)
    if not is_within_directory(base_resolved, candidate_resolved):
        raise UnsafeArchiveError(
            "检测到压缩包路径穿越（Zip Slip），已拒绝该条目",
            entries=[entry_name],
            detail=f"规范化后的目标路径位于目标目录之外：{candidate_resolved}",
        )
    return candidate


def _resolve_for_compare(path: Path) -> Path:
    try:
        return Path(os.path.normcase(os.path.normpath(os.path.abspath(str(path)))))
    except (OSError, ValueError):  # pragma: no cover - 极端路径
        return path


def is_within_directory(base: Path, candidate: Path) -> bool:
    """判断 ``candidate`` 是否位于 ``base`` 内部（含相等）。"""

    base_str = str(_resolve_for_compare(base))
    candidate_str = str(_resolve_for_compare(candidate))
    if base_str == candidate_str:
        return True
    if not base_str.endswith(os.sep):
        base_str += os.sep
    return candidate_str.startswith(base_str)


def check_disk_space(
    target_dir: Path, required_bytes: int, *, safety_margin: float = 0.02
) -> DiskSpaceReport:
    """检查目标目录所在磁盘的可用空间。"""

    probe = target_dir
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    usage = shutil.disk_usage(str(probe))
    needed = int(required_bytes * (1 + safety_margin))
    return DiskSpaceReport(
        target=target_dir,
        required=needed,
        available=usage.free,
        sufficient=usage.free >= needed,
    )


def require_disk_space(
    target_dir: Path, required_bytes: int, *, safety_margin: float = 0.02
) -> DiskSpaceReport:
    """空间不足时抛出 :class:`InsufficientSpaceError`，否则返回检查结果。"""

    report = check_disk_space(target_dir, required_bytes, safety_margin=safety_margin)
    if not report.sufficient:
        raise InsufficientSpaceError(
            "⚠ 磁盘空间不足，已阻止开始任务：" + report.message(),
            required=report.required,
            available=report.available,
        )
    return report


def required_space_for_entries(entries: Iterable[ArchiveEntry], *, total_size: int | None = None) -> int:
    if total_size is not None:
        return max(total_size, 0)
    return sum(entry.size for entry in entries if not entry.is_dir)


def scan_archive(info: ArchiveInfo, limits: SafetyLimits | None = None) -> list[SecurityIssue]:
    """对压缩包做静态安全检查（不写出任何文件）。"""

    limits = limits or SafetyLimits()
    issues: list[SecurityIssue] = []

    unsafe: list[str] = []
    long_names: list[str] = []
    for entry in info.entries:
        if validate_entry_name(entry.name, limits) is not None:
            unsafe.append(entry.name)
        if len(entry.name) > 259:
            long_names.append(entry.name)
    if unsafe:
        issues.append(
            SecurityIssue(
                level=IssueLevel.DANGER,
                kind="path_traversal",
                message="压缩包包含越界或非法路径，解压时会被拒绝",
                entries=unsafe,
            )
        )
    if long_names:
        issues.append(
            SecurityIssue(
                level=IssueLevel.WARNING,
                kind="long_path",
                message="压缩包包含超长路径条目，Windows 上可能需要启用长路径支持",
                entries=long_names,
            )
        )

    symlinks = [entry.name for entry in info.entries if entry.is_symlink or entry.is_hardlink]
    if symlinks:
        issues.append(
            SecurityIssue(
                level=IssueLevel.WARNING if limits.allow_symlinks else IssueLevel.DANGER,
                kind="symlink",
                message="压缩包包含符号链接/硬链接条目，默认不会解压这些条目",
                entries=symlinks,
            )
        )

    if len(info.entries) > limits.max_entries:
        issues.append(
            SecurityIssue(
                level=IssueLevel.WARNING,
                kind="too_many_entries",
                message=(
                    f"压缩包条目数量为 {len(info.entries)}，超过建议上限 {limits.max_entries}，请确认来源可信"
                ),
            )
        )

    total_size = info.total_size
    compressed = info.total_compressed_size or info.size_bytes
    if total_size > limits.block_total_size:
        issues.append(
            SecurityIssue(
                level=IssueLevel.DANGER,
                kind="size_limit",
                message=(
                    f"解压后总大小为 {human_size(total_size)}，超过安全上限 "
                    f"{human_size(limits.block_total_size)}"
                ),
            )
        )
    elif total_size > limits.warn_total_size:
        issues.append(
            SecurityIssue(
                level=IssueLevel.WARNING,
                kind="size_limit",
                message=f"解压后总大小为 {human_size(total_size)}，请确认磁盘空间是否充足",
            )
        )

    if compressed > 0 and total_size > 0:
        ratio = total_size / compressed
        if ratio >= limits.block_compression_ratio:
            issues.append(
                SecurityIssue(
                    level=IssueLevel.DANGER,
                    kind="zip_bomb",
                    message=(
                        f"压缩率异常（约 {ratio:.0f}:1），疑似解压炸弹，"
                        "PackPilot 已阻止默认解压，请确认来源可信"
                    ),
                )
            )
        elif ratio >= limits.warn_compression_ratio:
            issues.append(
                SecurityIssue(
                    level=IssueLevel.WARNING,
                    kind="zip_bomb",
                    message=f"压缩率较高（约 {ratio:.0f}:1），解压前请确认来源可信",
                )
            )
    return issues


def has_blocking_issue(issues: Iterable[SecurityIssue]) -> bool:
    return any(issue.level is IssueLevel.DANGER for issue in issues)


def ensure_entry_parent(path: Path) -> Path:
    """为解压目标创建父目录，必要时使用 Windows 扩展长度路径。"""

    parent = path.parent
    try:
        ensure_directory(parent)
    except OSError:
        ensure_directory(extended_length_path(parent))
    return path


class ConflictDecision(str, Enum):
    """冲突处理结果。"""

    OVERWRITE = "overwrite"
    SKIP = "skip"
    RENAME = "rename"


AskCallback = Callable[[Path], ConflictDecision]


def resolve_conflict(
    path: Path,
    policy: OverwritePolicy,
    *,
    ask: AskCallback | None = None,
    existing_paths: set[str] | None = None,
) -> tuple[Path | None, ConflictDecision]:
    """根据策略解决文件冲突；返回（最终路径或 ``None``，处理方式）。"""

    already_seen = existing_paths is not None and str(path).lower() in existing_paths
    if not path.exists() and not already_seen:
        return path, ConflictDecision.OVERWRITE

    if policy is OverwritePolicy.ASK:
        decision = ask(path) if ask is not None else ConflictDecision.RENAME
    elif policy is OverwritePolicy.OVERWRITE:
        decision = ConflictDecision.OVERWRITE
    elif policy is OverwritePolicy.SKIP:
        decision = ConflictDecision.SKIP
    elif policy is OverwritePolicy.FAIL:
        raise UnsafeArchiveError(
            f"目标文件已存在，按设置停止解压：{path}",
            entries=[path.name],
        )
    else:
        decision = ConflictDecision.RENAME

    if decision is ConflictDecision.SKIP:
        return None, decision
    if decision is ConflictDecision.RENAME:
        from app.core.utils import unique_child_path

        return unique_child_path(path.parent, path.name), decision
    return path, decision


def describe_issues(issues: Iterable[SecurityIssue]) -> str:
    """把安全检查结果拼成适合对话框展示的文本。"""

    lines = [str(issue) for issue in issues]
    return "\n".join(lines) if lines else "未发现安全问题"
