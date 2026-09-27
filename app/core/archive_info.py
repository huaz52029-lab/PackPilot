"""压缩格式枚举、条目与压缩包元数据。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePosixPath

_VOLUME_SUFFIX_RE = re.compile(r"\.(\d{3,4})$")


def strip_volume_suffix(name: str) -> str:
    """去掉 ``.001`` 形式的分卷后缀。"""

    return _VOLUME_SUFFIX_RE.sub("", name)


def volume_suffix_of(name: str) -> str | None:
    """返回分卷编号（``.001`` → ``001``），若不是分卷则为 ``None``。"""

    match = _VOLUME_SUFFIX_RE.search(name)
    return match.group(1) if match else None


class ArchiveFormat(str, Enum):
    """PackPilot 支持的压缩格式。"""

    ZIP = "zip"
    SEVEN_ZIP = "7z"
    TAR = "tar"
    TAR_GZ = "tar.gz"
    TAR_BZ2 = "tar.bz2"
    TAR_XZ = "tar.xz"

    @property
    def display_name(self) -> str:
        return {
            ArchiveFormat.ZIP: "ZIP",
            ArchiveFormat.SEVEN_ZIP: "7Z",
            ArchiveFormat.TAR: "TAR",
            ArchiveFormat.TAR_GZ: "TAR.GZ",
            ArchiveFormat.TAR_BZ2: "TAR.BZ2",
            ArchiveFormat.TAR_XZ: "TAR.XZ",
        }[self]

    @property
    def suffixes(self) -> tuple[str, ...]:
        return {
            ArchiveFormat.ZIP: (".zip",),
            ArchiveFormat.SEVEN_ZIP: (".7z",),
            ArchiveFormat.TAR: (".tar",),
            ArchiveFormat.TAR_GZ: (".tar.gz", ".tgz"),
            ArchiveFormat.TAR_BZ2: (".tar.bz2", ".tbz2", ".tbz"),
            ArchiveFormat.TAR_XZ: (".tar.xz", ".txz"),
        }[self]

    @property
    def default_suffix(self) -> str:
        return self.suffixes[0]

    @property
    def is_tar(self) -> bool:
        return self in {
            ArchiveFormat.TAR,
            ArchiveFormat.TAR_GZ,
            ArchiveFormat.TAR_BZ2,
            ArchiveFormat.TAR_XZ,
        }

    @property
    def tar_mode(self) -> str:
        """tarfile 写入模式。"""

        return {
            ArchiveFormat.TAR: "w",
            ArchiveFormat.TAR_GZ: "w:gz",
            ArchiveFormat.TAR_BZ2: "w:bz2",
            ArchiveFormat.TAR_XZ: "w:xz",
        }.get(self, "w")

    @property
    def supports_password_write(self) -> bool:
        """只有 7Z（py7zr / 7zAES）支持写入密码。"""

        return self is ArchiveFormat.SEVEN_ZIP

    @property
    def supports_password_read(self) -> bool:
        """ZIP 支持读取传统 ZipCrypto，7Z 支持 AES-256。"""

        return self in {ArchiveFormat.ZIP, ArchiveFormat.SEVEN_ZIP}

    @property
    def supports_compressed_size(self) -> bool:
        """TAR 系列不记录单条目压缩后大小。"""

        return not self.is_tar

    @property
    def password_note(self) -> str:
        if self is ArchiveFormat.SEVEN_ZIP:
            return "7Z 使用 py7zr 提供的 AES-256 加密，可同时加密文件头。"
        if self is ArchiveFormat.ZIP:
            return (
                "Python 标准库 zipfile 无法写入加密 ZIP，PackPilot 不提供 ZIP 写入密码；"
                "读取时支持传统 ZipCrypto 加密包，AES 加密 ZIP 不受支持。"
            )
        return "TAR 系列格式不支持密码保护，如需加密请使用 7Z。"

    @classmethod
    def from_path(cls, path: Path | str) -> ArchiveFormat | None:
        name = strip_volume_suffix(Path(path).name).lower()
        ordered = sorted(cls, key=lambda item: max(len(suffix) for suffix in item.suffixes), reverse=True)
        for archive_format in ordered:
            if any(name.endswith(suffix) for suffix in archive_format.suffixes):
                return archive_format
        return None

    @classmethod
    def all_suffixes(cls) -> list[str]:
        suffixes: list[str] = []
        for archive_format in cls:
            suffixes.extend(archive_format.suffixes)
        return suffixes


@dataclass(slots=True)
class ArchiveEntry:
    """压缩包内的单个条目。"""

    name: str
    is_dir: bool = False
    size: int = 0
    compressed_size: int | None = None
    mtime: datetime | None = None
    crc: int | None = None
    is_encrypted: bool = False
    is_symlink: bool = False
    is_hardlink: bool = False

    @property
    def ratio(self) -> float | None:
        if self.is_dir or not self.compressed_size or not self.size:
            return None
        return self.compressed_size / self.size

    @property
    def basename(self) -> str:
        return PurePosixPath(self.name.rstrip("/")).name or self.name

    @property
    def parent(self) -> str:
        parent = PurePosixPath(self.name.rstrip("/")).parent
        return "" if str(parent) == "." else str(parent)


@dataclass(slots=True)
class ArchiveInfo:
    """压缩包整体信息。"""

    path: Path
    format: ArchiveFormat
    entries: list[ArchiveEntry] = field(default_factory=list)
    comment: str = ""
    encrypted: bool = False
    size_bytes: int = 0
    mtime: datetime | None = None
    volumes: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    readable_path: Path | None = None

    @property
    def source_path(self) -> Path:
        """实际用于读取的路径（分卷时为合并后的临时文件）。"""

        return self.readable_path or self.path

    @property
    def is_volume_set(self) -> bool:
        return len(self.volumes) > 1

    @property
    def file_count(self) -> int:
        return sum(1 for entry in self.entries if not entry.is_dir)

    @property
    def dir_count(self) -> int:
        return sum(1 for entry in self.entries if entry.is_dir)

    @property
    def total_size(self) -> int:
        return sum(entry.size for entry in self.entries if not entry.is_dir)

    @property
    def total_compressed_size(self) -> int:
        total = sum(
            entry.compressed_size for entry in self.entries if not entry.is_dir and entry.compressed_size
        )
        return total or self.size_bytes

    @property
    def compression_ratio(self) -> float | None:
        compressed = self.total_compressed_size
        if not compressed or not self.total_size:
            return None
        return compressed / self.total_size

    @property
    def has_symlinks(self) -> bool:
        return any(entry.is_symlink or entry.is_hardlink for entry in self.entries)

    def names(self) -> list[str]:
        return [entry.name for entry in self.entries]

    def file_names(self) -> list[str]:
        return [entry.name for entry in self.entries if not entry.is_dir]

    def entry_map(self) -> dict[str, ArchiveEntry]:
        return {entry.name.rstrip("/"): entry for entry in self.entries}

    def top_level_names(self) -> list[str]:
        names: list[str] = []
        for entry in self.entries:
            first = entry.name.lstrip("./").split("/", 1)[0]
            if first and first not in names:
                names.append(first)
        return names

    def common_root(self) -> str | None:
        """若所有条目位于同一顶层目录下，返回该目录名。"""

        tops = self.top_level_names()
        if len(tops) != 1:
            return None
        top = tops[0]
        for entry in self.entries:
            name = entry.name.lstrip("./").rstrip("/")
            if name == top:
                if entry.is_dir:
                    continue
                return None
            if "/" not in name:
                return None
        return top
