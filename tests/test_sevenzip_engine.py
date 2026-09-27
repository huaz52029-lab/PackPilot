"""7Z 引擎测试：创建、解压、密码、错误密码、CRC、追加与删除。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.archive_info import ArchiveFormat
from app.core.errors import ArchiveCorruptedError, PasswordRequiredError, WrongPasswordError
from app.core.options import CompressionLevel, CreateOptions, ExtractOptions, OverwritePolicy
from app.core.sevenzip_engine import SevenZipEngine


def test_sevenzip_create_and_extract(tmp_path: Path, sample_tree: Path) -> None:
    engine = SevenZipEngine()
    target = tmp_path / "输出.7z"
    engine.create(
        CreateOptions(
            target=target,
            sources=[sample_tree],
            format=ArchiveFormat.SEVEN_ZIP,
            level=CompressionLevel.NORMAL,
        )
    )
    assert target.exists() and target.stat().st_size > 0
    info = engine.list_entries(target)
    assert info.format is ArchiveFormat.SEVEN_ZIP
    assert info.file_count == 3

    out = tmp_path / "解压"
    result = engine.extract(target, ExtractOptions(target=out, policy=OverwritePolicy.RENAME))
    assert result.extracted_files == 3
    assert (out / "测试项目" / "中文 文件夹" / "测试文件.txt").read_text(
        encoding="utf-8"
    ) == "PackPilot 中文路径测试\n"
    assert (out / "测试项目" / "空目录").is_dir()


def test_sevenzip_test_and_crc(tmp_path: Path, sample_tree: Path) -> None:
    engine = SevenZipEngine()
    target = tmp_path / "输出.7z"
    engine.create(CreateOptions(target=target, sources=[sample_tree]))
    report = engine.test(target)
    assert report.ok
    assert report.checked == report.total == 3
    assert any("数据校验正常" in note for note in report.notes)


def test_sevenzip_password_protection(tmp_path: Path, sample_tree: Path) -> None:
    engine = SevenZipEngine()
    target = tmp_path / "加密.7z"
    engine.create(
        CreateOptions(
            target=target, sources=[sample_tree], password="Str0ng!Passw0rd", format=ArchiveFormat.SEVEN_ZIP
        )
    )
    assert engine.is_encrypted(target) is True

    with pytest.raises(PasswordRequiredError):
        engine.list_entries(target)

    info = engine.list_entries(target, password="Str0ng!Passw0rd")
    assert info.encrypted is True
    assert info.file_count == 3

    with pytest.raises(WrongPasswordError):
        engine.list_entries(target, password="错误密码")


def test_sevenzip_extract_with_password(tmp_path: Path, sample_tree: Path) -> None:
    engine = SevenZipEngine()
    target = tmp_path / "加密.7z"
    engine.create(CreateOptions(target=target, sources=[sample_tree], password="test-密码-123"))
    out = tmp_path / "解压"
    result = engine.extract(
        target,
        ExtractOptions(target=out, password="test-密码-123", policy=OverwritePolicy.RENAME),
    )
    assert result.extracted_files == 3
    assert (out / "测试项目" / "中文 文件夹" / "测试文件.txt").exists()


def test_sevenzip_wrong_password_on_extract(tmp_path: Path, sample_tree: Path) -> None:
    engine = SevenZipEngine()
    target = tmp_path / "加密.7z"
    engine.create(CreateOptions(target=target, sources=[sample_tree], password="正确密码"))
    with pytest.raises(WrongPasswordError):
        engine.extract(
            target,
            ExtractOptions(target=tmp_path / "解压", password="错误密码", policy=OverwritePolicy.RENAME),
        )


def test_sevenzip_add_and_delete(tmp_path: Path, sample_tree: Path, simple_file: Path) -> None:
    engine = SevenZipEngine()
    target = tmp_path / "输出.7z"
    engine.create(CreateOptions(target=target, sources=[sample_tree]))
    added = engine.add_files(target, [simple_file])
    assert added == ["简单文件.txt"]
    assert "简单文件.txt" in engine.list_entries(target).names()

    removed = engine.delete_entries(target, ["测试项目/中文 文件夹"])
    assert removed >= 1
    names = engine.list_entries(target).names()
    assert not any("中文 文件夹" in name for name in names)
    assert "简单文件.txt" in names


def test_sevenzip_add_to_encrypted_archive(tmp_path: Path, simple_file: Path) -> None:
    engine = SevenZipEngine()
    target = tmp_path / "加密.7z"
    engine.create(
        CreateOptions(
            target=target, sources=[simple_file], password="pw-123456", format=ArchiveFormat.SEVEN_ZIP
        )
    )
    added = engine.add_files(target, [simple_file], password="pw-123456")
    assert added == ["简单文件 (1).txt"]
    info = engine.list_entries(target, password="pw-123456")
    assert info.file_count == 2


def test_sevenzip_corrupted_file(tmp_path: Path) -> None:
    broken = tmp_path / "损坏.7z"
    broken.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 64)
    engine = SevenZipEngine()
    with pytest.raises(ArchiveCorruptedError):
        engine.list_entries(broken)


def test_sevenzip_large_file_roundtrip(tmp_path: Path, large_file: Path) -> None:
    engine = SevenZipEngine()
    target = tmp_path / "大.7z"
    engine.create(CreateOptions(target=target, sources=[large_file], level=CompressionLevel.FASTEST))
    out = tmp_path / "解压"
    engine.extract(target, ExtractOptions(target=out, policy=OverwritePolicy.OVERWRITE))
    assert (out / large_file.name).read_bytes() == large_file.read_bytes()
