"""分卷压缩测试：切分、完整性校验、缺失分卷、零拷贝读取与解压。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core.archive_info import ArchiveFormat
from app.core.archive_manager import ArchiveManager
from app.core.errors import VolumeMissingError
from app.core.options import CompressionLevel, CreateOptions, ExtractOptions, OverwritePolicy
from app.core.volumes import (
    VolumeReader,
    inspect_volume_set,
    is_volume_part,
    open_volume_reader,
    split_file,
    verify_volume_set,
    volume_base,
)


@pytest.fixture
def large_source(tmp_path: Path) -> Path:
    folder = tmp_path / "分卷数据"
    folder.mkdir()
    (folder / "随机数据.bin").write_bytes(os.urandom(200_000))
    (folder / "说明.txt").write_text("分卷测试", encoding="utf-8")
    return folder


def test_split_creates_parts_and_manifest(tmp_path: Path, large_source: Path) -> None:
    engine = ArchiveManager()
    target = tmp_path / "分卷.zip"
    result = engine.create_archive(
        CreateOptions(
            target=target,
            sources=[large_source],
            format=ArchiveFormat.ZIP,
            level=CompressionLevel.FASTEST,
            volume_size=50_000,
        )
    )
    assert result.volume_set is not None
    assert len(result.volume_set.parts) >= 4
    for part in result.volume_set.parts:
        assert part.path.exists()
        assert is_volume_part(part.path)
    assert not target.exists(), "分卷后不应保留完整压缩包"
    manifest = target.with_name(target.name + ".packpilot-parts.json")
    assert manifest.exists()


def test_volume_inspect_and_verify(tmp_path: Path, large_source: Path) -> None:
    manager = ArchiveManager()
    target = tmp_path / "分卷.zip"
    manager.create_archive(
        CreateOptions(
            target=target,
            sources=[large_source],
            format=ArchiveFormat.ZIP,
            volume_size=40_000,
        )
    )
    first = target.with_name(target.name + ".001")
    volume_set = inspect_volume_set(first)
    assert volume_set.complete
    assert volume_base(first) == target
    verified = verify_volume_set(first)
    assert verified.complete
    assert all(part.sha256 for part in verified.parts)


def test_missing_volume_is_reported(tmp_path: Path, large_source: Path) -> None:
    manager = ArchiveManager()
    target = tmp_path / "分卷.zip"
    manager.create_archive(
        CreateOptions(target=target, sources=[large_source], format=ArchiveFormat.ZIP, volume_size=40_000)
    )
    parts = sorted(tmp_path.glob("分卷.zip.0*"))
    assert len(parts) >= 3
    removed = parts[1]
    backup = removed.read_bytes()
    removed.unlink()
    try:
        volume_set = inspect_volume_set(parts[0])
        assert not volume_set.complete
        assert volume_set.missing == [removed.name]
        with pytest.raises(VolumeMissingError) as exc:
            manager.list_archive(parts[0])
        assert removed.name in str(exc.value)
        assert removed.name in exc.value.missing_parts
    finally:
        removed.write_bytes(backup)
    # 恢复后应可正常读取
    info = manager.list_archive(parts[0])
    assert info.is_volume_set
    assert info.file_count == 2


def test_volume_reader_reads_across_parts(tmp_path: Path) -> None:
    source = tmp_path / "数据.bin"
    source.write_bytes(bytes(range(256)) * 1000)
    volume_set = split_file(source, part_size=10_000, keep_original=True)
    assert len(volume_set.parts) > 2
    with open_volume_reader(volume_set) as reader:
        assert isinstance(reader, VolumeReader)
        assert reader.read(100) == (bytes(range(256)) * 1000)[:100]
        reader.seek(9990)
        position = reader.tell()
        assert position == 9990
        chunk = reader.read(30)
        assert chunk == (bytes(range(256)) * 1000)[9990:10020]
        reader.seek(-10, os.SEEK_END)
        assert len(reader.read()) == 10


def test_extract_from_volumes(tmp_path: Path, large_source: Path) -> None:
    manager = ArchiveManager()
    target = tmp_path / "分卷.zip"
    manager.create_archive(
        CreateOptions(target=target, sources=[large_source], format=ArchiveFormat.ZIP, volume_size=45_000)
    )
    first = target.with_name(target.name + ".001")
    out = tmp_path / "解压"
    result = manager.extract_archive(first, ExtractOptions(target=out, policy=OverwritePolicy.RENAME))
    assert result.extracted_files == 2
    assert (out / "分卷数据" / "说明.txt").read_text(encoding="utf-8") == "分卷测试"
    report = manager.test_archive(first)
    assert report.ok


@pytest.mark.parametrize(
    "archive_format",
    [ArchiveFormat.ZIP, ArchiveFormat.SEVEN_ZIP, ArchiveFormat.TAR_GZ],
)
def test_volume_roundtrip_all_formats(
    tmp_path: Path, large_source: Path, archive_format: ArchiveFormat
) -> None:
    manager = ArchiveManager()
    target = tmp_path / f"分卷{archive_format.default_suffix}"
    manager.create_archive(
        CreateOptions(
            target=target,
            sources=[large_source],
            format=archive_format,
            volume_size=60_000,
        )
    )
    first = target.with_name(target.name + ".001")
    info = manager.list_archive(first)
    assert info.file_count == 2
    out = tmp_path / f"解压-{archive_format.value}"
    result = manager.extract_archive(first, ExtractOptions(target=out, policy=OverwritePolicy.RENAME))
    assert result.extracted_files == 2
    assert manager.test_archive(first).ok


def test_split_then_verify_detects_corrupted_part(tmp_path: Path) -> None:
    source = tmp_path / "数据.bin"
    source.write_bytes(os.urandom(30_000))
    volume_set = split_file(source, part_size=10_000, keep_original=True)
    part = volume_set.parts[0].path
    data = bytearray(part.read_bytes())
    data[0] ^= 0xFF
    part.write_bytes(bytes(data))
    verified = verify_volume_set(part)
    assert not verified.complete
    assert any("SHA-256" in problem or "校验失败" in problem for problem in verified.problems)
