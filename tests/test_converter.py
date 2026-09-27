"""格式转换测试：ZIP ↔ 7Z ↔ TAR，失败时保留源文件。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.archive_info import ArchiveFormat
from app.core.archive_manager import ArchiveManager
from app.core.converter import ArchiveConverter
from app.core.errors import PackPilotError
from app.core.options import CompressionLevel, ConvertOptions, CreateOptions, ExtractOptions, OverwritePolicy


@pytest.fixture
def manager() -> ArchiveManager:
    return ArchiveManager()


@pytest.fixture
def zip_archive(tmp_path: Path, sample_tree: Path, manager: ArchiveManager) -> Path:
    target = tmp_path / "源.zip"
    manager.create_archive(CreateOptions(target=target, sources=[sample_tree]))
    return target


@pytest.mark.parametrize(
    ("source_format", "target_format"),
    [
        (ArchiveFormat.ZIP, ArchiveFormat.SEVEN_ZIP),
        (ArchiveFormat.SEVEN_ZIP, ArchiveFormat.ZIP),
        (ArchiveFormat.TAR, ArchiveFormat.ZIP),
        (ArchiveFormat.ZIP, ArchiveFormat.TAR),
        (ArchiveFormat.ZIP, ArchiveFormat.TAR_GZ),
    ],
)
def test_convert_between_formats(
    tmp_path: Path,
    sample_tree: Path,
    manager: ArchiveManager,
    source_format: ArchiveFormat,
    target_format: ArchiveFormat,
) -> None:
    source = tmp_path / f"源{source_format.default_suffix}"
    manager.create_archive(CreateOptions(target=source, sources=[sample_tree], format=source_format))
    target = tmp_path / f"目标{target_format.default_suffix}"
    converter = ArchiveConverter(manager)
    result = converter.convert(ConvertOptions(source=source, target=target, level=CompressionLevel.FAST))
    assert result.target == target
    assert source.exists(), "转换不应删除源文件"
    info = manager.list_archive(target)
    assert info.format is target_format
    assert info.file_count == 3
    out = tmp_path / "解压"
    extract_result = manager.extract_archive(
        target, ExtractOptions(target=out, policy=OverwritePolicy.RENAME)
    )
    assert extract_result.extracted_files == 3
    assert (out / "测试项目" / "中文 文件夹" / "测试文件.txt").exists()


def test_convert_reports_monotonic_progress(
    tmp_path: Path, zip_archive: Path, manager: ArchiveManager
) -> None:
    target = tmp_path / "目标.7z"
    values: list[int] = []
    ArchiveConverter(manager).convert(
        ConvertOptions(source=zip_archive, target=target),
        progress=lambda done, total, _message: values.append(done),
    )
    assert values
    assert values[-1] == 100
    assert values == sorted(values)


def test_convert_encrypted_7z_to_zip(tmp_path: Path, sample_tree: Path, manager: ArchiveManager) -> None:
    source = tmp_path / "加密.7z"
    manager.create_archive(
        CreateOptions(
            target=source, sources=[sample_tree], format=ArchiveFormat.SEVEN_ZIP, password="密码123456"
        )
    )
    target = tmp_path / "输出.zip"
    ArchiveConverter(manager).convert(
        ConvertOptions(source=source, target=target, source_password="密码123456")
    )
    info = manager.list_archive(target)
    assert info.file_count == 3
    assert not info.encrypted


def test_convert_with_volume_split(tmp_path: Path, sample_tree: Path, manager: ArchiveManager) -> None:
    source = tmp_path / "源.zip"
    manager.create_archive(CreateOptions(target=source, sources=[sample_tree]))
    target = tmp_path / "目标.7z"
    result = ArchiveConverter(manager).convert(ConvertOptions(source=source, target=target, volume_size=200))
    assert result.volume_set is not None
    assert len(result.volume_set.parts) >= 2
    assert not target.exists(), "分卷后不应保留完整压缩包"
    first = target.with_name(target.name + ".001")
    info = manager.list_archive(first)
    assert info.file_count == 3


def test_convert_same_path_rejected(tmp_path: Path, zip_archive: Path, manager: ArchiveManager) -> None:
    with pytest.raises(PackPilotError):
        ArchiveConverter(manager).convert(ConvertOptions(source=zip_archive, target=zip_archive))


def test_convert_failure_keeps_source(tmp_path: Path, zip_archive: Path, manager: ArchiveManager) -> None:
    from app.core import converter as converter_module

    target = tmp_path / "目标.7z"

    def boom(*_args: object, **_kwargs: object) -> None:
        raise PackPilotError("模拟失败")

    original = manager.create_archive
    manager.create_archive = boom  # type: ignore[assignment]
    try:
        with pytest.raises(PackPilotError):
            converter_module.ArchiveConverter(manager).convert(
                ConvertOptions(source=zip_archive, target=target)
            )
    finally:
        manager.create_archive = original  # type: ignore[assignment]
    assert zip_archive.exists(), "转换失败时源文件必须保留"
    assert not target.exists(), "转换失败时不应留下半成品"
