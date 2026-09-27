"""ArchiveManager 门面测试：智能解压、内部文件打开、条目校验与磁盘检查。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.archive_info import ArchiveFormat
from app.core.archive_manager import ArchiveManager
from app.core.errors import EntryNotFoundError, PackPilotError, UnsupportedFormatError
from app.core.options import (
    CompressionLevel,
    CreateOptions,
    ExtractOptions,
    OverwritePolicy,
)
from app.core.smart_extract import archive_stem, plan_smart_extract, suggested_extract_directory


@pytest.fixture
def manager() -> ArchiveManager:
    return ArchiveManager()


@pytest.fixture
def archive(tmp_path: Path, sample_tree: Path, manager: ArchiveManager) -> Path:
    target = tmp_path / "示例.zip"
    manager.create_archive(CreateOptions(target=target, sources=[sample_tree]))
    return target


def test_detect_format() -> None:
    manager = ArchiveManager()
    assert manager.detect_format(Path("a.zip")) is ArchiveFormat.ZIP
    assert manager.detect_format(Path("a.7z")) is ArchiveFormat.SEVEN_ZIP
    assert manager.detect_format(Path("a.tar.gz")) is ArchiveFormat.TAR_GZ
    assert manager.detect_format(Path("a.tgz")) is ArchiveFormat.TAR_GZ
    assert manager.detect_format(Path("a.tar.bz2")) is ArchiveFormat.TAR_BZ2
    assert manager.detect_format(Path("a.tar.xz")) is ArchiveFormat.TAR_XZ
    with pytest.raises(UnsupportedFormatError):
        manager.detect_format(Path("a.rar"))


def test_list_and_extract(archive: Path, tmp_path: Path, manager: ArchiveManager) -> None:
    info = manager.list_archive(archive)
    assert info.file_count == 3
    assert info.total_size > 0
    assert info.compression_ratio is not None
    out = tmp_path / "解压"
    result = manager.extract_archive(archive, ExtractOptions(target=out, policy=OverwritePolicy.RENAME))
    assert result.extracted_files == 3
    assert out.exists()


def test_extract_missing_entry_raises(archive: Path, tmp_path: Path, manager: ArchiveManager) -> None:
    with pytest.raises(EntryNotFoundError):
        manager.extract_archive(
            archive,
            ExtractOptions(target=tmp_path / "out", entries=["不存在的条目.txt"]),
        )


def test_estimate_extract_size(archive: Path, manager: ArchiveManager) -> None:
    total = manager.estimate_extract_size(archive)
    selected = manager.estimate_extract_size(archive, ["测试项目/中文 文件夹"])
    assert total > selected > 0


def test_extract_entry_to_temp(archive: Path, tmp_path: Path, manager: ArchiveManager) -> None:
    workspace = tmp_path / "临时"
    path = manager.extract_entry_to_temp(archive, "测试项目/中文 文件夹/测试文件.txt", workspace)
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "PackPilot 中文路径测试\n"
    assert workspace in path.parents


def test_extract_entry_to_temp_rejects_directory(
    archive: Path, tmp_path: Path, manager: ArchiveManager
) -> None:
    with pytest.raises(PackPilotError):
        manager.extract_entry_to_temp(archive, "测试项目/中文 文件夹", tmp_path / "临时")


def test_extract_entry_missing(archive: Path, tmp_path: Path, manager: ArchiveManager) -> None:
    with pytest.raises(EntryNotFoundError):
        manager.extract_entry_to_temp(archive, "不存在.txt", tmp_path / "临时")


def test_add_and_delete_via_manager(archive: Path, simple_file: Path, manager: ArchiveManager) -> None:
    added = manager.add_files(archive, [simple_file])
    assert added == ["简单文件.txt"]
    removed = manager.delete_entries(archive, ["简单文件.txt"])
    assert removed == 1
    assert "简单文件.txt" not in manager.list_archive(archive).names()


def test_security_scan_and_password_detection(
    tmp_path: Path, sample_tree: Path, manager: ArchiveManager
) -> None:
    encrypted = tmp_path / "加密.7z"
    manager.create_archive(
        CreateOptions(
            target=encrypted, sources=[sample_tree], format=ArchiveFormat.SEVEN_ZIP, password="pw123456"
        )
    )
    assert manager.is_encrypted(encrypted) is True
    assert manager.verify_password(encrypted, "pw123456") is True
    assert manager.verify_password(encrypted, "错误") is False


def test_open_nonexistent_file(manager: ArchiveManager) -> None:
    from app.core.errors import PackPilotError as PPError

    with pytest.raises(PPError):
        manager.list_archive(Path("不存在的文件.zip"))


def test_smart_extract_plan_with_common_root(
    tmp_path: Path, sample_tree: Path, manager: ArchiveManager
) -> None:
    archive = tmp_path / "项目.zip"
    manager.create_archive(CreateOptions(target=archive, sources=[sample_tree]))
    info = manager.list_archive(archive)
    plan = plan_smart_extract(archive, info)
    assert plan.directory == tmp_path
    assert "顶层目录" in plan.reason
    assert not plan.uses_archive_stem


def test_smart_extract_plan_for_root_files(tmp_path: Path, manager: ArchiveManager) -> None:
    source = tmp_path / "散文件"
    source.mkdir()
    for index in range(5):
        (source / f"file{index}.txt").write_text("x", encoding="utf-8")
    archive = tmp_path / "散文件.zip"
    manager.create_archive(CreateOptions(target=archive, sources=[source], include_root=False))
    info = manager.list_archive(archive)
    plan = plan_smart_extract(archive, info)
    assert plan.directory == tmp_path / "散文件"
    assert plan.uses_archive_stem


def test_smart_extract_plan_single_file(tmp_path: Path, simple_file: Path, manager: ArchiveManager) -> None:
    archive = tmp_path / "单文件.zip"
    manager.create_archive(CreateOptions(target=archive, sources=[simple_file]))
    info = manager.list_archive(archive)
    plan = plan_smart_extract(archive, info)
    assert plan.directory == tmp_path


def test_archive_stem_variants() -> None:
    assert archive_stem(Path("a.zip")) == "a"
    assert archive_stem(Path("a.tar.gz")) == "a"
    assert archive_stem(Path("a.tar.bz2")) == "a"
    assert archive_stem(Path("project.zip.001")) == "project"
    assert archive_stem(Path("无扩展名")) == "无扩展名"


def test_suggested_directory_without_smart(
    tmp_path: Path, sample_tree: Path, manager: ArchiveManager
) -> None:
    archive = tmp_path / "示例.zip"
    manager.create_archive(CreateOptions(target=archive, sources=[sample_tree]))
    info = manager.list_archive(archive)
    assert suggested_extract_directory(archive, info, smart=False) == tmp_path / "示例"
    assert suggested_extract_directory(archive, info, smart=True) == tmp_path


def test_volume_operations_require_volume_set(tmp_path: Path, archive: Path, manager: ArchiveManager) -> None:
    with pytest.raises(PackPilotError):
        manager.verify_volumes(archive)


def test_add_files_blocked_for_volume_set(tmp_path: Path, simple_file: Path, manager: ArchiveManager) -> None:
    source = tmp_path / "数据"
    source.mkdir()
    (source / "big.bin").write_bytes(bytes(range(256)) * 400)
    target = tmp_path / "分卷.zip"
    manager.create_archive(CreateOptions(target=target, sources=[source], volume_size=20_000))
    first = target.with_name(target.name + ".001")
    with pytest.raises(PackPilotError):
        manager.add_files(first, [simple_file])


def test_create_archive_levels_and_volume_options(
    tmp_path: Path, sample_tree: Path, manager: ArchiveManager
) -> None:
    for level in CompressionLevel:
        target = tmp_path / f"{level.value}.zip"
        result = manager.create_archive(CreateOptions(target=target, sources=[sample_tree], level=level))
        assert result.target.exists()
