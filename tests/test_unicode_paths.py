"""中文与特殊路径测试（覆盖 ``D:\\测试项目\\中文 文件夹\\测试文件.txt`` 场景）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.archive_info import ArchiveFormat
from app.core.archive_manager import ArchiveManager
from app.core.checksum import hash_file
from app.core.options import CreateOptions, ExtractOptions, OverwritePolicy

CHINESE_NAMES = [
    "测试项目",
    "中文 文件夹",
    "带空格的 目录",
    "日本語フォルダ",
    "한국어폴더",
    "emoji-📦-folder",
]


@pytest.mark.parametrize("folder_name", CHINESE_NAMES)
@pytest.mark.parametrize(
    "archive_format",
    [ArchiveFormat.ZIP, ArchiveFormat.SEVEN_ZIP, ArchiveFormat.TAR_GZ],
)
def test_unicode_folder_roundtrip(tmp_path: Path, folder_name: str, archive_format: ArchiveFormat) -> None:
    source = tmp_path / folder_name
    source.mkdir()
    file_path = source / "测试文件.txt"
    file_path.write_text(f"内容：{folder_name}\n", encoding="utf-8")
    (source / "子目录 带空格").mkdir()
    (source / "子目录 带空格" / "数据 文件.bin").write_bytes("数据".encode())

    manager = ArchiveManager()
    archive = tmp_path / f"归档{archive_format.default_suffix}"
    manager.create_archive(CreateOptions(target=archive, sources=[source], format=archive_format))
    info = manager.list_archive(archive)
    assert info.file_count == 2
    assert any(folder_name in name for name in info.names())

    out = tmp_path / "解压输出"
    result = manager.extract_archive(archive, ExtractOptions(target=out, policy=OverwritePolicy.RENAME))
    assert result.extracted_files == 2
    restored = out / folder_name / "测试文件.txt"
    assert restored.read_text(encoding="utf-8") == f"内容：{folder_name}\n"
    assert (out / folder_name / "子目录 带空格" / "数据 文件.bin").exists()


def test_deep_chinese_path_with_spaces(tmp_path: Path) -> None:
    """模拟 ``D:\\测试项目\\中文 文件夹\\测试文件.txt`` 结构。"""

    root = tmp_path / "测试项目"
    nested = root / "中文 文件夹"
    nested.mkdir(parents=True)
    target = nested / "测试文件.txt"
    target.write_text("深度路径测试", encoding="utf-8")

    manager = ArchiveManager()
    archive = tmp_path / "深度路径.zip"
    manager.create_archive(CreateOptions(target=archive, sources=[root]))
    out = tmp_path / "输出 目录"
    manager.extract_archive(archive, ExtractOptions(target=out, policy=OverwritePolicy.OVERWRITE))
    restored = out / "测试项目" / "中文 文件夹" / "测试文件.txt"
    assert restored.read_text(encoding="utf-8") == "深度路径测试"
    assert hash_file(restored, "md5").digest == hash_file(target, "md5").digest


def test_unicode_archive_name_and_volume(tmp_path: Path) -> None:
    source = tmp_path / "压缩 源文件.txt"
    source.write_text("分卷中文测试" * 500, encoding="utf-8")
    manager = ArchiveManager()
    archive = tmp_path / "中文 压缩包.zip"
    result = manager.create_archive(CreateOptions(target=archive, sources=[source], volume_size=300))
    assert result.volume_set is not None
    first = archive.with_name(archive.name + ".001")
    assert first.exists()
    info = manager.list_archive(first)
    assert info.file_count == 1
    out = tmp_path / "解压"
    manager.extract_archive(first, ExtractOptions(target=out, policy=OverwritePolicy.RENAME))
    assert (out / source.name).read_text(encoding="utf-8") == "分卷中文测试" * 500


def test_special_characters_and_spaces(tmp_path: Path) -> None:
    source = tmp_path / "特殊 目录 #1 & (2)"
    source.mkdir()
    (source / "文件 ~!@#$%^&()+=-.txt").write_text("special", encoding="utf-8")
    manager = ArchiveManager()
    archive = tmp_path / "特殊.zip"
    manager.create_archive(CreateOptions(target=archive, sources=[source]))
    out = tmp_path / "解压"
    manager.extract_archive(archive, ExtractOptions(target=out, policy=OverwritePolicy.RENAME))
    assert (out / source.name / "文件 ~!@#$%^&()+=-.txt").read_text(encoding="utf-8") == "special"
