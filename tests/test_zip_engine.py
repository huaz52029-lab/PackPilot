"""ZIP 引擎测试：创建、解压、测试、追加、删除、重复文件、冲突策略。"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from app.core.archive_info import ArchiveFormat
from app.core.errors import ArchiveCorruptedError, OperationNotSupportedError, TaskCancelledError
from app.core.options import CompressionLevel, CreateOptions, ExtractOptions, OverwritePolicy
from app.core.zip_engine import ZipEngine


def test_create_and_list_zip(tmp_path: Path, sample_tree: Path) -> None:
    engine = ZipEngine()
    target = tmp_path / "输出.zip"
    engine.create(CreateOptions(target=target, sources=[sample_tree], level=CompressionLevel.NORMAL))

    assert target.exists() and target.stat().st_size > 0
    info = engine.list_entries(target)
    assert info.format is ArchiveFormat.ZIP
    assert info.file_count == 3
    names = info.names()
    assert "测试项目/中文 文件夹/测试文件.txt" in names
    assert "测试项目/空目录/" in names
    assert any(name.endswith("空文件.txt") for name in names)


def test_extract_zip_roundtrip(tmp_path: Path, sample_tree: Path) -> None:
    engine = ZipEngine()
    target = tmp_path / "输出.zip"
    engine.create(CreateOptions(target=target, sources=[sample_tree]))
    out = tmp_path / "解压"
    result = engine.extract(target, ExtractOptions(target=out, policy=OverwritePolicy.RENAME))

    assert result.extracted_files == 3
    extracted = out / "测试项目" / "中文 文件夹" / "测试文件.txt"
    assert extracted.read_text(encoding="utf-8") == "PackPilot 中文路径测试\n"
    assert (out / "测试项目" / "空目录").is_dir()


def test_zip_compression_levels_produce_files(tmp_path: Path, simple_file: Path) -> None:
    engine = ZipEngine()
    sizes = {}
    for level in CompressionLevel:
        target = tmp_path / f"{level.value}.zip"
        engine.create(CreateOptions(target=target, sources=[simple_file], level=level))
        sizes[level] = target.stat().st_size
    assert all(size > 0 for size in sizes.values())
    assert sizes[CompressionLevel.FASTEST] >= sizes[CompressionLevel.ULTRA]


def test_zip_password_write_is_rejected(tmp_path: Path, simple_file: Path) -> None:
    engine = ZipEngine()
    with pytest.raises(OperationNotSupportedError) as exc:
        engine.create(CreateOptions(target=tmp_path / "加密.zip", sources=[simple_file], password="secret"))
    assert "zipfile" in str(exc.value)


def test_zip_test_detects_corruption(tmp_path: Path, sample_tree: Path) -> None:
    engine = ZipEngine()
    target = tmp_path / "输出.zip"
    engine.create(CreateOptions(target=target, sources=[sample_tree]))
    report = engine.test(target)
    assert report.ok and report.checked == report.total

    data = bytearray(target.read_bytes())
    # 破坏第一个文件条目的压缩数据
    data[80:120] = b"\x00" * 40
    broken = tmp_path / "损坏.zip"
    broken.write_bytes(bytes(data))
    broken_report = engine.test(broken)
    assert not broken_report.ok
    assert broken_report.failures


def test_add_files_and_duplicate_names(tmp_path: Path, simple_file: Path) -> None:
    engine = ZipEngine()
    target = tmp_path / "输出.zip"
    engine.create(CreateOptions(target=target, sources=[simple_file]))
    added = engine.add_files(target, [simple_file, simple_file])
    # 同一次调用中选择同一文件不会被重复加入，但已存在的同名条目会重命名
    assert added == ["简单文件 (1).txt"]
    added_again = engine.add_files(target, [simple_file])
    assert added_again == ["简单文件 (2).txt"]
    info = engine.list_entries(target)
    assert info.file_count == 3
    assert len({entry.name for entry in info.entries}) == 3


def test_delete_entries_including_directory(tmp_path: Path, sample_tree: Path) -> None:
    engine = ZipEngine()
    target = tmp_path / "输出.zip"
    engine.create(CreateOptions(target=target, sources=[sample_tree]))
    removed = engine.delete_entries(target, ["测试项目/中文 文件夹"])
    # 目录条目本身 + 目录内两个文件
    assert removed == 3
    info = engine.list_entries(target)
    assert not any("中文 文件夹" in entry.name for entry in info.entries)
    assert engine.test(target).ok


def test_extract_policies_skip_and_overwrite(tmp_path: Path, simple_file: Path) -> None:
    engine = ZipEngine()
    target = tmp_path / "输出.zip"
    engine.create(CreateOptions(target=target, sources=[simple_file]))
    out = tmp_path / "解压"
    (out).mkdir()
    existing = out / "简单文件.txt"
    existing.write_text("原始内容", encoding="utf-8")

    skipped = engine.extract(target, ExtractOptions(target=out, policy=OverwritePolicy.SKIP))
    assert skipped.skipped == 1
    assert existing.read_text(encoding="utf-8") == "原始内容"

    renamed = engine.extract(target, ExtractOptions(target=out, policy=OverwritePolicy.RENAME))
    assert renamed.renamed == 1
    assert (out / "简单文件 (1).txt").exists()

    overwritten = engine.extract(target, ExtractOptions(target=out, policy=OverwritePolicy.OVERWRITE))
    assert overwritten.extracted_files == 1
    assert existing.read_text(encoding="utf-8") == "PackPilot\n"


def test_extract_selected_entries_only(tmp_path: Path, sample_tree: Path) -> None:
    engine = ZipEngine()
    target = tmp_path / "输出.zip"
    engine.create(CreateOptions(target=target, sources=[sample_tree]))
    out = tmp_path / "解压"
    result = engine.extract(
        target,
        ExtractOptions(
            target=out,
            entries=["测试项目/中文 文件夹"],
            policy=OverwritePolicy.OVERWRITE,
        ),
    )
    assert result.extracted_files == 2
    assert not (out / "测试项目" / "子目录").exists()


def test_cancel_during_extraction_removes_partial_file(tmp_path: Path, large_file: Path) -> None:
    engine = ZipEngine()
    target = tmp_path / "大.zip"
    engine.create(CreateOptions(target=target, sources=[large_file]))
    out = tmp_path / "解压"
    calls = {"count": 0}

    def cancel_check() -> bool:
        calls["count"] += 1
        return calls["count"] > 2

    with pytest.raises(TaskCancelledError):
        engine.extract(
            target,
            ExtractOptions(target=out, policy=OverwritePolicy.RENAME),
            is_cancelled=cancel_check,
        )
    assert not (out / "大文件.bin").exists()


def test_invalid_zip_raises(tmp_path: Path) -> None:
    broken = tmp_path / "坏文件.zip"
    broken.write_bytes("这不是压缩包".encode())
    engine = ZipEngine()
    with pytest.raises(ArchiveCorruptedError):
        engine.list_entries(broken)
    with pytest.raises(ArchiveCorruptedError):
        engine.test(broken)


def test_zip_comment_roundtrip(tmp_path: Path, simple_file: Path) -> None:
    engine = ZipEngine()
    target = tmp_path / "带注释.zip"
    engine.create(CreateOptions(target=target, sources=[simple_file], comment="PackPilot 注释"))
    info = engine.list_entries(target)
    assert info.comment == "PackPilot 注释"


def test_zip_stores_utf8_chinese_names(tmp_path: Path, sample_tree: Path) -> None:
    engine = ZipEngine()
    target = tmp_path / "中文.zip"
    engine.create(CreateOptions(target=target, sources=[sample_tree]))
    with zipfile.ZipFile(target) as archive:
        assert any("中文 文件夹" in name for name in archive.namelist())
    extracted = tmp_path / "再解压"
    engine.extract(target, ExtractOptions(target=extracted, policy=OverwritePolicy.OVERWRITE))
    assert (extracted / "测试项目" / "中文 文件夹" / "测试文件.txt").exists()


def test_zip_preserves_empty_file_and_directory(tmp_path: Path, sample_tree: Path) -> None:
    engine = ZipEngine()
    target = tmp_path / "空内容.zip"
    engine.create(CreateOptions(target=target, sources=[sample_tree]))
    out = tmp_path / "解压"
    engine.extract(target, ExtractOptions(target=out, policy=OverwritePolicy.OVERWRITE))
    assert (out / "测试项目" / "中文 文件夹" / "空文件.txt").stat().st_size == 0
    assert (out / "测试项目" / "空目录").is_dir()


def test_zip_special_characters_in_names(tmp_path: Path) -> None:
    source = tmp_path / "特殊字符 #$&()[]{}@!+~；，"
    source.mkdir()
    (source / "a b c#1&2 (test).txt").write_text("special", encoding="utf-8")
    engine = ZipEngine()
    target = tmp_path / "特殊.zip"
    engine.create(CreateOptions(target=target, sources=[source]))
    out = tmp_path / "解压"
    engine.extract(target, ExtractOptions(target=out, policy=OverwritePolicy.OVERWRITE))
    assert (out / source.name / "a b c#1&2 (test).txt").read_text(encoding="utf-8") == "special"


def test_large_file_roundtrip(tmp_path: Path, large_file: Path) -> None:
    engine = ZipEngine()
    target = tmp_path / "大文件.zip"
    engine.create(CreateOptions(target=target, sources=[large_file], level=CompressionLevel.FASTEST))
    out = tmp_path / "解压"
    engine.extract(target, ExtractOptions(target=out, policy=OverwritePolicy.OVERWRITE))
    assert (out / large_file.name).stat().st_size == large_file.stat().st_size
    assert (out / large_file.name).read_bytes() == large_file.read_bytes()
    assert os.path.getsize(target) > 0
