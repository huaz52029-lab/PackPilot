"""TAR 系列引擎测试（TAR / TAR.GZ / TAR.BZ2 / TAR.XZ）。"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from app.core.archive_info import ArchiveFormat
from app.core.errors import ArchiveCorruptedError, OperationNotSupportedError
from app.core.options import CompressionLevel, CreateOptions, ExtractOptions, OverwritePolicy
from app.core.tar_engine import TarEngine

FORMATS = [
    ArchiveFormat.TAR,
    ArchiveFormat.TAR_GZ,
    ArchiveFormat.TAR_BZ2,
    ArchiveFormat.TAR_XZ,
]


@pytest.mark.parametrize("archive_format", FORMATS)
def test_tar_create_extract_roundtrip(
    tmp_path: Path, sample_tree: Path, archive_format: ArchiveFormat
) -> None:
    engine = TarEngine(archive_format)
    target = tmp_path / f"输出{archive_format.default_suffix}"
    engine.create(
        CreateOptions(
            target=target, sources=[sample_tree], format=archive_format, level=CompressionLevel.FAST
        )
    )
    assert target.exists() and target.stat().st_size > 0

    info = engine.list_entries(target)
    assert info.format is archive_format
    assert info.file_count == 3

    out = tmp_path / f"解压-{archive_format.value.replace('.', '-')}"
    result = engine.extract(target, ExtractOptions(target=out, policy=OverwritePolicy.RENAME))
    assert result.extracted_files == 3
    assert (out / "测试项目" / "中文 文件夹" / "测试文件.txt").exists()
    assert (out / "测试项目" / "空目录").is_dir()


@pytest.mark.parametrize("archive_format", FORMATS)
def test_tar_test_reports_ok(tmp_path: Path, simple_file: Path, archive_format: ArchiveFormat) -> None:
    engine = TarEngine(archive_format)
    target = tmp_path / f"输出{archive_format.default_suffix}"
    engine.create(CreateOptions(target=target, sources=[simple_file], format=archive_format))
    report = engine.test(target)
    assert report.ok
    assert report.checked == report.total >= 1


def test_tar_password_not_supported(tmp_path: Path, simple_file: Path) -> None:
    engine = TarEngine(ArchiveFormat.TAR_GZ)
    with pytest.raises(OperationNotSupportedError):
        engine.create(
            CreateOptions(target=tmp_path / "输出.tar.gz", sources=[simple_file], password="secret")
        )


def test_tar_compression_levels(tmp_path: Path, large_file: Path) -> None:
    engine = TarEngine(ArchiveFormat.TAR_GZ)
    fastest = tmp_path / "fast.tar.gz"
    best = tmp_path / "best.tar.gz"
    engine.create(CreateOptions(target=fastest, sources=[large_file], level=CompressionLevel.FASTEST))
    engine.create(CreateOptions(target=best, sources=[large_file], level=CompressionLevel.ULTRA))
    assert fastest.exists() and best.exists()


def test_tar_add_and_delete(tmp_path: Path, sample_tree: Path, simple_file: Path) -> None:
    engine = TarEngine(ArchiveFormat.TAR)
    target = tmp_path / "输出.tar"
    engine.create(CreateOptions(target=target, sources=[sample_tree], format=ArchiveFormat.TAR))
    added = engine.add_files(target, [simple_file])
    assert added == ["简单文件.txt"]
    assert "简单文件.txt" in engine.list_entries(target).names()

    removed = engine.delete_entries(target, ["测试项目/中文 文件夹"])
    assert removed >= 1
    assert not any("中文 文件夹" in name for name in engine.list_entries(target).names())
    assert engine.test(target).ok


def test_tar_skips_symlink_members(tmp_path: Path) -> None:
    source = tmp_path / "real.txt"
    source.write_text("真实内容", encoding="utf-8")
    archive_path = tmp_path / "链接.tar"
    with tarfile.open(archive_path, "w") as archive:
        archive.add(source, arcname="real.txt")
        info = tarfile.TarInfo("link.txt")
        info.type = tarfile.SYMTYPE
        info.linkname = "real.txt"
        archive.addfile(info)

    engine = TarEngine(ArchiveFormat.TAR)
    listed = engine.list_entries(archive_path)
    assert any(entry.is_symlink for entry in listed.entries)
    out = tmp_path / "解压"
    result = engine.extract(archive_path, ExtractOptions(target=out, policy=OverwritePolicy.RENAME))
    assert result.skipped >= 1
    assert (out / "real.txt").exists()
    assert not (out / "link.txt").exists()
    assert any("符号链接" in warning for warning in result.warnings)


def test_tar_corrupted_file_detected(tmp_path: Path) -> None:
    broken = tmp_path / "损坏.tar.gz"
    broken.write_bytes(b"\x1f\x8b\x08\x00broken")
    engine = TarEngine(ArchiveFormat.TAR_GZ)
    with pytest.raises(ArchiveCorruptedError):
        engine.list_entries(broken)
    with pytest.raises(ArchiveCorruptedError):
        engine.test(broken)


def test_tar_empty_archive_supported(tmp_path: Path) -> None:
    empty_dir = tmp_path / "空目录"
    empty_dir.mkdir()
    engine = TarEngine(ArchiveFormat.TAR)
    target = tmp_path / "空.tar"
    engine.create(CreateOptions(target=target, sources=[empty_dir], format=ArchiveFormat.TAR))
    info = engine.list_entries(target)
    assert info.file_count == 0
    assert info.dir_count >= 1
    report = engine.test(target)
    assert report.ok
