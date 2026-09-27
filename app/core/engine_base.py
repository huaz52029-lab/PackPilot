"""压缩引擎基类与创建归档时的公共遍历逻辑。"""

from __future__ import annotations

import contextlib
import io
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from app.core.archive_info import ArchiveFormat, ArchiveInfo
from app.core.errors import OperationNotSupportedError, TaskCancelledError
from app.core.options import CreateOptions, ExtractOptions, ExtractResult, TestReport

ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]
AskCallback = Callable[[Path], object]

CHUNK_SIZE = 1024 * 1024

#: 压缩包来源：既可以是磁盘路径，也可以是分卷读取器之类的可随机读取文件对象。
type ArchiveSource = Path | BinaryIO


def source_name(source: object) -> str:
    """返回压缩包来源的显示名（用于提示与日志）。"""

    if isinstance(source, (str, Path)):
        return Path(source).name
    name = getattr(source, "name", None)
    if name:
        return Path(str(name)).name
    return "分卷集合"


def source_as_path(source: object) -> Path | None:
    if isinstance(source, (str, Path)):
        return Path(source)
    return None


def source_size(source: object) -> int:
    """返回压缩包字节数（分卷读取器返回合并后的总大小）。"""

    path = source_as_path(source)
    if path is not None:
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = getattr(source, "total_size", None)
    if isinstance(total, int):
        return total
    try:
        current = source.tell()  # type: ignore[union-attr]
        source.seek(0, 2)  # type: ignore[union-attr]
        size = source.tell()  # type: ignore[union-attr]
        source.seek(current)  # type: ignore[union-attr]
        return int(size)
    except (AttributeError, OSError, ValueError):
        return 0


def source_mtime(source: object) -> datetime | None:
    path = source_as_path(source)
    if path is None:
        return None
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


def rewind_source(source: object) -> None:
    """把文件对象来源定位到开头（第三方库可能从当前位置开始读取）。"""

    if isinstance(source, (str, Path)):
        return
    seek = getattr(source, "seek", None)
    if callable(seek):
        with contextlib.suppress(OSError, ValueError):
            seek(0)


def noop_progress(done: int, total: int, message: str) -> None:
    """默认进度回调（什么也不做）。"""


def never_cancelled() -> bool:
    """默认取消检查（永不取消）。"""

    return False


@dataclass(slots=True)
class CreateItem:
    """待写入压缩包的单个条目。"""

    fs_path: Path
    arcname: str
    is_dir: bool
    size: int


def _sorted_children(directory: Path) -> list[Path]:
    try:
        children = list(directory.iterdir())
    except OSError:
        return []
    return sorted(children, key=lambda item: (item.is_file(), item.name.lower()))


def _walk_directory(root: Path, prefix: str) -> Iterator[CreateItem]:
    for child in _sorted_children(root):
        arcname = f"{prefix}/{child.name}"
        try:
            is_dir = child.is_dir()
        except OSError:
            continue
        if child.is_symlink():
            if is_dir:
                continue
            try:
                size = child.stat().st_size
            except OSError:
                continue
            yield CreateItem(child, arcname, False, size)
            continue
        if is_dir:
            yield CreateItem(child, arcname, True, 0)
            yield from _walk_directory(child, arcname)
            continue
        try:
            size = child.stat().st_size
        except OSError:
            continue
        yield CreateItem(child, arcname, False, size)


def iter_create_items(sources: Sequence[Path], *, include_root: bool = True) -> Iterator[CreateItem]:
    """把用户选择的文件/文件夹展开为压缩包条目列表。

    ``include_root=False`` 时，文件夹自身不作为顶层目录写入（用于格式转换等场景）。
    """

    seen: set[str] = set()
    for source in sources:
        source = Path(source)
        if not source.exists():
            raise FileNotFoundError(f"找不到要压缩的项目：{source}")
        if source.is_dir():
            if include_root:
                arcname = source.name
                key = arcname.lower()
                if key in seen:
                    continue
                seen.add(key)
                yield CreateItem(source, arcname, True, 0)
                yield from _walk_directory(source, arcname)
            else:
                yield from iter_tree_items(source)
        else:
            arcname = source.name
            key = arcname.lower()
            if key in seen:
                continue
            seen.add(key)
            yield CreateItem(source, arcname, False, source.stat().st_size)


def count_create_items(sources: Sequence[Path], *, include_root: bool = True) -> tuple[int, int]:
    """返回（文件数量，总字节数），用于进度与磁盘估算。"""

    files = 0
    total = 0
    for item in iter_create_items(sources, include_root=include_root):
        if item.is_dir:
            continue
        files += 1
        total += item.size
    return files, total


def iter_tree_items(root: Path, prefix: str = "") -> Iterator[CreateItem]:
    """遍历目录内容，arcname 相对于 ``root``（用于重写压缩包）。"""

    root = Path(root)
    for child in _sorted_children(root):
        arcname = f"{prefix}/{child.name}" if prefix else child.name
        if child.is_symlink() and child.is_dir():
            continue
        if child.is_dir():
            yield CreateItem(child, arcname, True, 0)
            yield from iter_tree_items(child, arcname)
        elif child.is_file():
            yield CreateItem(child, arcname, False, child.stat().st_size)


def unique_arcname(arcname: str, existing: set[str]) -> str:
    """当压缩包内已存在同名条目时生成 ``名称 (1).ext`` 形式的条目名。"""

    candidate = arcname
    index = 1
    while candidate.rstrip("/").lower() in existing:
        stem, dot, extension = arcname.rpartition(".")
        candidate = f"{arcname} ({index})" if not dot or "/" in extension else f"{stem} ({index}).{extension}"
        index += 1
    return candidate


def expand_selection(all_names: Sequence[str], entries: Sequence[str] | None) -> set[str] | None:
    """把用户选择的条目展开为完整集合（选择目录时包含其所有子项）。"""

    if not entries:
        return None
    known = {name.rstrip("/"): name for name in all_names}
    selected: set[str] = set()
    for entry in entries:
        normalized = entry.rstrip("/")
        if normalized in known:
            selected.add(known[normalized])
        prefix = normalized + "/"
        selected.update(name for name in all_names if name.rstrip("/").startswith(prefix))
    return selected


def selection_set(
    all_names: Sequence[str], entries: Sequence[str] | None, *, exact: bool = False
) -> set[str] | None:
    """计算选中的条目集合；``exact=True`` 时不展开目录子项。"""

    if not entries:
        return None
    if not exact:
        return expand_selection(all_names, entries)
    known = {name.rstrip("/"): name for name in all_names}
    return {known.get(entry.rstrip("/"), entry) for entry in entries}


def selection_filter(info: ArchiveInfo, entries: Sequence[str] | None) -> list[str] | None:
    """把用户选择的条目展开为按压缩包顺序排列的列表。"""

    selected = expand_selection(info.names(), entries)
    if selected is None:
        return None
    return [name for name in info.names() if name in selected]


class ProgressReader(io.BufferedIOBase):
    """包装文件对象，用于在上层读取时报告进度并支持取消。"""

    def __init__(
        self,
        handle: BinaryIO,
        *,
        on_chunk: Callable[[int], None],
        is_cancelled: CancelCheck,
        name: str = "",
    ) -> None:
        self._handle = handle
        self._on_chunk = on_chunk
        self._is_cancelled = is_cancelled
        self.name = name or getattr(handle, "name", "")

    def read(self, size: int = -1) -> bytes:
        if self._is_cancelled():
            raise TaskCancelledError("任务已取消")
        data = self._handle.read(size)
        if data:
            self._on_chunk(len(data))
        return data

    def readinto(self, buffer: bytearray) -> int:
        data = self.read(len(buffer))
        size = len(data)
        buffer[:size] = data
        return size

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._handle.seek(offset, whence)

    def tell(self) -> int:
        return self._handle.tell()

    def fileno(self) -> int:
        return self._handle.fileno()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> ProgressReader:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class ArchiveEngine(ABC):
    """压缩引擎接口：所有格式共用同一套调用方式。"""

    format: ArchiveFormat

    @abstractmethod
    def list_entries(self, source: ArchiveSource, *, password: str | None = None) -> ArchiveInfo:
        """读取压缩包结构与条目信息。"""

    @abstractmethod
    def create(
        self,
        options: CreateOptions,
        *,
        progress: ProgressCallback = noop_progress,
        is_cancelled: CancelCheck = never_cancelled,
    ) -> Path:
        """创建压缩包，返回生成的路径。"""

    @abstractmethod
    def extract(
        self,
        source: ArchiveSource,
        options: ExtractOptions,
        *,
        progress: ProgressCallback = noop_progress,
        is_cancelled: CancelCheck = never_cancelled,
        ask: AskCallback | None = None,
    ) -> ExtractResult:
        """解压到目标目录。"""

    @abstractmethod
    def test(
        self,
        source: ArchiveSource,
        *,
        password: str | None = None,
        progress: ProgressCallback = noop_progress,
        is_cancelled: CancelCheck = never_cancelled,
    ) -> TestReport:
        """完整读取并校验压缩包。"""

    def add_files(
        self,
        path: Path,
        sources: Sequence[Path],
        *,
        level: object = None,
        password: str | None = None,
        progress: ProgressCallback = noop_progress,
        is_cancelled: CancelCheck = never_cancelled,
    ) -> list[str]:
        """向已有压缩包追加文件，返回新增条目名。"""

        raise OperationNotSupportedError(f"{self.format.display_name} 格式暂不支持向已有压缩包追加文件")

    def delete_entries(
        self,
        path: Path,
        names: Sequence[str],
        *,
        password: str | None = None,
        progress: ProgressCallback = noop_progress,
        is_cancelled: CancelCheck = never_cancelled,
    ) -> int:
        """删除指定条目，返回删除数量。"""

        raise OperationNotSupportedError(f"{self.format.display_name} 格式暂不支持删除条目")
