"""分卷压缩：切分、完整性校验、缺失检测与零拷贝读取。

PackPilot 采用与 7-Zip 一致的 ``project.zip.001`` / ``.002`` 字节切分方式，
并额外写入 ``*.packpilot-parts.json`` 清单文件（记录每卷大小与 SHA-256），
用于精确的完整性检查与缺失分卷提示。清单缺失时仍可按序号扫描并尝试恢复。
"""

from __future__ import annotations

import bisect
import hashlib
import io
import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from app.core.archive_info import ArchiveFormat, strip_volume_suffix, volume_suffix_of
from app.core.errors import PackPilotError, TaskCancelledError, VolumeError, VolumeMissingError
from app.core.utils import human_size, read_json

MANIFEST_SUFFIX = ".packpilot-parts.json"
MANIFEST_FORMAT = "PackPilot-VolumeSet"
MANIFEST_VERSION = 1
_PART_RE = re.compile(r"^(?P<base>.+)\.(?P<index>\d{3,4})$")
_CHUNK = 1024 * 1024


def manifest_path_for(base: Path) -> Path:
    return base.with_name(base.name + MANIFEST_SUFFIX)


def is_volume_part(path: Path | str) -> bool:
    """判断路径是否为 ``xxx.zip.001`` 形式的分卷文件。"""

    name = Path(path).name
    if volume_suffix_of(name) is None:
        return False
    base_name = strip_volume_suffix(name)
    return ArchiveFormat.from_path(base_name) is not None


def volume_base(path: Path) -> Path | None:
    """返回分卷对应的基准文件名（``project.zip.001`` → ``project.zip``）。"""

    path = Path(path)
    if volume_suffix_of(path.name) is None:
        return None
    return path.with_name(strip_volume_suffix(path.name))


@dataclass(slots=True)
class VolumePart:
    """单个分卷文件。"""

    index: int
    path: Path
    size: int
    sha256: str | None = None


@dataclass(slots=True)
class VolumeSet:
    """分卷集合的状态。"""

    base: Path
    parts: list[VolumePart] = field(default_factory=list)
    manifest: dict[str, object] | None = None
    problems: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(part.size for part in self.parts)

    @property
    def complete(self) -> bool:
        return not self.missing and not self.problems

    @property
    def part_names(self) -> list[str]:
        return [part.path.name for part in self.parts]

    def describe(self) -> str:
        if self.complete:
            return f"分卷完整：{len(self.parts)} 卷，共 {human_size(self.total_size)}"
        details: list[str] = []
        if self.missing:
            details.append("缺失分卷：" + "、".join(self.missing))
        if self.problems:
            details.extend(self.problems)
        return "；".join(details)


def _digit_width(names: list[str]) -> int:
    widths = [len(volume_suffix_of(name) or "") for name in names]
    return max(widths) if widths else 3


def find_parts(base: Path, *, digit_width: int | None = None) -> list[VolumePart]:
    """按序号扫描已存在的分卷文件（跳过清单等非数字后缀文件，允许序号缺口）。"""

    base = Path(base)
    parts: list[VolumePart] = []
    pattern = re.compile(r"^" + re.escape(base.name) + r"\.(?P<index>\d{3,4})$")
    for child in base.parent.glob(base.name + ".*"):
        match = pattern.match(child.name)
        if match is None:
            continue
        index = int(match.group("index"))
        if digit_width is not None and len(match.group("index")) != digit_width:
            continue
        if not child.is_file():
            continue
        parts.append(VolumePart(index=index, path=child, size=child.stat().st_size))
    parts.sort(key=lambda part: part.index)
    return parts


def load_manifest(base: Path) -> dict[str, object] | None:
    payload = read_json(manifest_path_for(base), None)
    if isinstance(payload, dict) and payload.get("format") == MANIFEST_FORMAT:
        return payload
    return None


def inspect_volume_set(path: Path, *, verify_hashes: bool = False) -> VolumeSet:
    """检查分卷集合：缺失、乱序、大小异常与清单一致性。"""

    base = volume_base(path) or Path(path)
    manifest = load_manifest(base)
    manifest_parts: list[dict[str, object]] = []
    if manifest is not None:
        raw_parts = manifest.get("parts")
        if isinstance(raw_parts, list):
            manifest_parts = [item for item in raw_parts if isinstance(item, dict)]

    found = {part.path.name: part for part in find_parts(base)}

    volume_set = VolumeSet(base=base, manifest=manifest)
    for index, item in enumerate(manifest_parts, start=1):
        name = str(item.get("name") or f"{base.name}.{index:03d}")
        expected_size = int(item.get("size") or 0)
        expected_hash = item.get("sha256")
        part = found.get(name)
        if part is None:
            volume_set.missing.append(name)
            continue
        if expected_size and part.size != expected_size:
            volume_set.problems.append(
                f"分卷大小异常：{name}（期望 {human_size(expected_size)}，实际 {human_size(part.size)}）"
            )
        if verify_hashes and isinstance(expected_hash, str) and expected_hash:
            actual = file_sha256(part.path)
            if actual != expected_hash:
                volume_set.problems.append(f"分卷校验失败（SHA-256 不匹配）：{name}")
        part.sha256 = expected_hash if isinstance(expected_hash, str) else None
        volume_set.parts.append(part)

    if manifest_parts:
        return volume_set

    # 无清单：依赖序号连续性
    parts = find_parts(base)
    volume_set.parts = parts
    if not parts:
        volume_set.problems.append(f"未找到任何分卷文件：{base.name}.001")
        return volume_set
    expected_count = int(manifest.get("part_count", 0)) if manifest else 0
    numbers = [part.index for part in parts]
    for expected in range(1, max(numbers) + 1):
        if expected not in numbers:
            volume_set.missing.append(f"{base.name}.{expected:03d}")
    if expected_count and len(parts) != expected_count:
        for index in range(len(parts) + 1, expected_count + 1):
            volume_set.missing.append(f"{base.name}.{index:03d}")
    standard = parts[0].size
    for part in parts[:-1]:
        if part.size != standard:
            volume_set.problems.append(
                f"分卷大小异常：{part.path.name}（{human_size(part.size)}，"
                f"其余分卷为 {human_size(standard)}）"
            )
    if parts[-1].size > standard:
        volume_set.problems.append(f"最后一个分卷大于前面分卷：{parts[-1].path.name}")
    return volume_set


def raise_if_incomplete(volume_set: VolumeSet) -> None:
    """分卷缺失或不完整时抛出明确异常。"""

    if volume_set.missing:
        raise VolumeMissingError(
            "分卷不完整，缺少文件：" + "、".join(volume_set.missing),
            missing_parts=volume_set.missing,
        )
    if volume_set.problems:
        raise VolumeError("分卷校验失败：" + "；".join(volume_set.problems))


def file_sha256(path: Path, *, chunk_size: int = _CHUNK) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_volume_set(
    path: Path,
    *,
    progress: object = None,
    is_cancelled: object = None,
) -> VolumeSet:
    """完整校验分卷集合（读取每个分卷计算 SHA-256）。"""

    volume_set = inspect_volume_set(path, verify_hashes=False)
    if not volume_set.parts:
        return volume_set
    total = len(volume_set.parts)
    for index, part in enumerate(volume_set.parts, start=1):
        if callable(is_cancelled) and is_cancelled():
            raise TaskCancelledError("校验任务已取消")
        digest = file_sha256(part.path)
        part.sha256 = digest
        if callable(progress):
            progress(index, total, part.path.name)
    manifest = volume_set.manifest
    if manifest and isinstance(manifest.get("parts"), list):
        expected = {
            str(item.get("name")): item.get("sha256")
            for item in manifest["parts"]  # type: ignore[union-attr]
            if isinstance(item, dict)
        }
        for part in volume_set.parts:
            expected_hash = expected.get(part.path.name)
            if isinstance(expected_hash, str) and expected_hash and expected_hash != part.sha256:
                volume_set.problems.append(f"分卷校验失败（SHA-256 不匹配）：{part.path.name}")
    return volume_set


def split_file(
    source: Path,
    *,
    part_size: int,
    progress: object = None,
    is_cancelled: object = None,
    keep_original: bool = False,
) -> VolumeSet:
    """把已生成的压缩包切分为分卷，并写入清单文件。"""

    if part_size <= 0:
        raise PackPilotError("分卷大小必须大于 0")
    source = Path(source)
    total_size = source.stat().st_size
    part_count = max(1, (total_size + part_size - 1) // part_size)
    width = max(3, len(str(part_count)))
    base = source
    parts: list[VolumePart] = []
    digest_index: list[dict[str, object]] = []
    done = 0
    with source.open("rb") as handle:
        for index in range(1, part_count + 1):
            if callable(is_cancelled) and is_cancelled():
                for part in parts:
                    part.path.unlink(missing_ok=True)
                raise TaskCancelledError("分卷任务已取消")
            target = base.with_name(f"{base.name}.{index:0{width}d}")
            digest = hashlib.sha256()
            remaining = min(part_size, total_size - done)
            written = 0
            with target.open("wb") as out:
                while written < remaining:
                    chunk = handle.read(min(_CHUNK, remaining - written))
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    done += len(chunk)
                    if callable(progress):
                        progress(done, total_size, target.name)
            parts.append(VolumePart(index=index, path=target, size=written, sha256=digest.hexdigest()))
            digest_index.append({"name": target.name, "size": written, "sha256": digest.hexdigest()})
    manifest = {
        "format": MANIFEST_FORMAT,
        "version": MANIFEST_VERSION,
        "base": base.name,
        "part_count": len(parts),
        "part_size": part_size,
        "total_size": total_size,
        "parts": digest_index,
    }
    manifest_target = manifest_path_for(base)
    manifest_target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    if not keep_original:
        source.unlink(missing_ok=True)
    return VolumeSet(base=base, parts=parts, manifest=manifest)


class VolumeReader(io.RawIOBase):
    """把多个分卷拼接成一个可随机读取的文件对象（不产生临时文件）。"""

    def __init__(self, parts: list[VolumePart]) -> None:
        super().__init__()
        self.name = parts[0].path.name if parts else "分卷集合"
        self._paths = [part.path for part in parts]
        self._sizes = [part.size for part in parts]
        self._starts: list[int] = []
        offset = 0
        for size in self._sizes:
            self._starts.append(offset)
            offset += size
        self._total = offset
        self._position = 0
        self._handle: io.BufferedReader | None = None
        self._handle_index = -1

    # -- 基本属性 ------------------------------------------------------
    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    @property
    def total_size(self) -> int:
        return self._total

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_SET:
            position = offset
        elif whence == os.SEEK_CUR:
            position = self._position + offset
        elif whence == os.SEEK_END:
            position = self._total + offset
        else:  # pragma: no cover - 非法 whence
            raise ValueError(f"不支持的 whence：{whence}")
        if position < 0:
            raise ValueError("分卷读取位置不能为负")
        self._position = position
        return self._position

    def readinto(self, buffer: bytearray) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file")
        if self._position >= self._total:
            return 0
        view = memoryview(buffer).cast("B")
        remaining = len(view)
        written = 0
        while remaining > 0 and self._position < self._total:
            index = bisect.bisect_right(self._starts, self._position) - 1
            index = max(index, 0)
            start = self._starts[index]
            offset_in_part = self._position - start
            available = self._sizes[index] - offset_in_part
            if available <= 0:
                self._position = start + self._sizes[index]
                continue
            take = min(remaining, available)
            handle = self._handle_for(index)
            handle.seek(offset_in_part)
            chunk = handle.read(take)
            if not chunk:
                break
            view[written : written + len(chunk)] = chunk
            written += len(chunk)
            remaining -= len(chunk)
            self._position += len(chunk)
        return written

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = self._total - self._position
        if size <= 0:
            return b""
        buffer = bytearray(size)
        count = self.readinto(buffer)
        return bytes(buffer[:count])

    def _handle_for(self, index: int) -> io.BufferedReader:
        if self._handle is not None and self._handle_index == index:
            return self._handle
        if self._handle is not None:
            self._handle.close()
        self._handle = self._paths[index].open("rb")
        self._handle_index = index
        return self._handle

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
            self._handle_index = -1
        super().close()


def open_volume_reader(volume_set: VolumeSet) -> VolumeReader:
    """为分卷集合创建可随机读取的文件对象。"""

    if not volume_set.parts:
        raise PackPilotError(f"未找到分卷文件：{volume_set.base.name}.001")
    return VolumeReader(volume_set.parts)


def iter_volume_candidates(base: Path) -> Iterator[Path]:
    """列出目录中与基准名匹配的所有潜在分卷（含序号缺口）。"""

    pattern = re.compile(re.escape(base.name) + r"\.(\d{3,4})$")
    for child in sorted(base.parent.glob(base.name + ".*")):
        if pattern.match(child.name):
            yield child
