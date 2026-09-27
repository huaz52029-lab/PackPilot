"""通用工具函数：大小/时间格式化、路径处理、原子写入。"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import tempfile
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

from app.core.errors import PackPilotError

logger = logging.getLogger(__name__)

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def human_size(size: int | float | None) -> str:
    """把字节数格式化为人类可读字符串。"""

    if size is None:
        return "-"
    value = float(size)
    if value < 0:
        return "-"
    for index, unit in enumerate(_UNITS):
        if value < 1024 or index == len(_UNITS) - 1:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} PB"  # pragma: no cover - 不可达


def human_duration(seconds: float | None) -> str:
    """把秒数格式化为中文时长。"""

    if seconds is None or seconds < 0:
        return "-"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} 小时 {minutes:02d} 分 {secs:02d} 秒"
    if minutes:
        return f"{minutes} 分 {secs:02d} 秒"
    return f"{secs} 秒"


def human_speed(bytes_per_second: float | None) -> str:
    if not bytes_per_second or bytes_per_second <= 0:
        return "-"
    return f"{human_size(bytes_per_second)}/s"


def format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S")


def now_string() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sanitize_filename(name: str, *, fallback: str = "archive") -> str:
    """去掉 Windows 文件名中的非法字符。"""

    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if char in invalid or ord(char) < 32 else char for char in name)
    cleaned = cleaned.strip().rstrip(".")
    return cleaned or fallback


def unique_path(path: Path) -> Path:
    """当目标已存在时生成 ``名称 (1).ext`` 形式的可用路径。"""

    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    parent = path.parent
    for index in range(1, 10_000):
        candidate = parent / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    raise PackPilotError(f"无法为 {path.name} 生成不冲突的文件名")


def unique_child_path(directory: Path, name: str) -> Path:
    """在目录内为 ``name`` 生成不冲突的路径（重命名冲突文件时使用）。"""

    return unique_path(directory / name)


def extended_length_path(path: Path) -> Path:
    """在 Windows 上返回带 ``\\\\?\\`` 前缀的路径，用于处理超长路径。"""

    if os.name != "nt":
        return path
    text = str(path)
    if text.startswith("\\\\?\\"):
        return path
    absolute = os.path.abspath(text)
    if absolute.startswith("\\\\"):  # UNC 路径
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def ensure_directory(path: Path) -> Path:
    """创建目录（支持 Windows 超长路径），并返回该目录。"""

    path = Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        extended = extended_length_path(path)
        extended.mkdir(parents=True, exist_ok=True)
    return path


def path_is_too_long(path: Path, limit: int = 259) -> bool:
    return len(str(path)) > limit


@contextlib.contextmanager
def temporary_directory(prefix: str = "packpilot-", parent: Path | None = None) -> Iterator[Path]:
    """创建临时目录并保证退出时清理。"""

    base = str(parent) if parent is not None else None
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=base))
    try:
        yield directory
    finally:
        remove_tree(directory)


def remove_tree(path: Path) -> None:
    """删除目录树，忽略删除失败（例如文件仍被占用）。"""

    if not path.exists():
        return

    def _on_error(func: Callable[[str], None], target: str, _exc_info: object) -> None:
        with contextlib.suppress(Exception):
            os.chmod(target, os.stat(target).st_mode | 0o700)
            func(target)

    with contextlib.suppress(Exception):
        shutil.rmtree(path, onerror=_on_error)


def atomic_write_json(path: Path, payload: object) -> None:
    """原子写入 JSON 文件，避免写入中断导致配置损坏。"""

    ensure_directory(path.parent)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        replace_file(tmp_path, path)
    finally:
        with contextlib.suppress(OSError):
            if tmp_path.exists():
                tmp_path.unlink()


def replace_file(source: Path, target: Path) -> None:
    """把 ``source`` 移动到 ``target``，兼容部分文件系统上 ``os.replace`` 失败的情况。

    某些环境（云同步目录、特定过滤驱动）会对同目录 ``MoveFileEx`` 返回
    ``WinError 17``；此时退化为“删除目标 + 重命名”，最后再退化为直接写入。
    """

    source = Path(source)
    target = Path(target)
    try:
        os.replace(source, target)
        return
    except OSError as first_error:
        with contextlib.suppress(OSError):
            if target.exists():
                target.unlink()
        try:
            os.replace(source, target)
            return
        except OSError:
            logger.debug("直接替换失败，改用复制方式：%s → %s", source, target, exc_info=True)
        try:
            if target.exists():
                target.unlink()
            with source.open("rb") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            with contextlib.suppress(OSError):
                os.fsync(target.open("rb").fileno())
            source.unlink(missing_ok=True)
        except OSError as exc:
            raise OSError(f"无法写入文件 {target}（{first_error}；回退方式同样失败：{exc}）") from exc


def read_json(path: Path, default: object) -> object:
    """读取 JSON 文件；文件缺失或损坏时返回 ``default``。"""

    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def iter_files(root: Path) -> Iterator[Path]:
    """深度优先遍历目录下所有文件（不进入符号链接目录）。"""

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = [name for name in dirnames if not (current / name).is_symlink()]
        for filename in filenames:
            yield current / filename


def iter_entries(root: Path) -> Iterator[Path]:
    """遍历目录下的文件与子目录（包含空目录）。"""

    yield root
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        keep: list[str] = []
        for dirname in dirnames:
            link = current / dirname
            if link.is_symlink():
                continue
            keep.append(dirname)
            yield link
        dirnames[:] = keep
        for filename in filenames:
            yield current / filename


def is_archive_name(name: str) -> bool:
    """根据扩展名判断文件名是否为受支持的压缩包（用于拖放与批量操作）。"""

    from app.core.archive_info import ArchiveFormat, strip_volume_suffix

    return ArchiveFormat.from_path(Path(strip_volume_suffix(name))) is not None


def count_tree(path: Path) -> tuple[int, int]:
    """统计目录内的文件数与总字节数。"""

    if path.is_file():
        try:
            return 1, path.stat().st_size
        except OSError:
            return 1, 0
    files = 0
    total = 0
    for child in iter_files(path):
        files += 1
        try:
            total += child.stat().st_size
        except OSError:
            continue
    return files, total
