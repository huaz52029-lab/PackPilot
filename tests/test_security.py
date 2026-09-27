"""安全测试：Zip Slip、绝对路径、UNC、符号链接、解压炸弹、磁盘空间与覆盖保护。"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

import pytest

from app.core.archive_manager import ArchiveManager
from app.core.archive_security import (
    IssueLevel,
    check_disk_space,
    is_safe_entry_name,
    require_disk_space,
    safe_join,
    scan_archive,
    validate_entry_name,
)
from app.core.errors import InsufficientSpaceError, UnsafeArchiveError
from app.core.options import (
    ExtractOptions,
    OverwritePolicy,
    SafetyLimits,
)
from app.core.tar_engine import TarEngine
from app.core.zip_engine import ZipEngine

MALICIOUS_NAMES = [
    "../../../../Windows/System32/test.dll",
    "..\\..\\Windows\\System32\\evil.dll",
    "/etc/passwd",
    "\\Windows\\System32\\drivers\\etc\\hosts",
    "C:\\Windows\\System32\\test.dll",
    "C:/Windows/System32/test.dll",
    "\\\\server\\share\\evil.txt",
    "//server/share/evil.txt",
    "normal/../../escape.txt",
    "folder/./../../escape2.txt",
]


@pytest.mark.parametrize("name", MALICIOUS_NAMES)
def test_validate_entry_name_rejects_traversal(name: str) -> None:
    assert not is_safe_entry_name(name)
    assert validate_entry_name(name) is not None


def test_validate_entry_name_accepts_normal_names() -> None:
    for name in ("a.txt", "文件夹/测试文件.txt", "a/b/c/d.bin", "带空格 文件.txt"):
        assert is_safe_entry_name(name)
        assert validate_entry_name(name) is None


def test_validate_entry_name_rejects_reserved_and_illegal() -> None:
    assert validate_entry_name("CON") is not None
    assert validate_entry_name("aux.txt") is not None
    assert validate_entry_name("bad<name>.txt") is not None
    assert validate_entry_name("trailing.") is not None
    assert validate_entry_name("trailing ") is not None
    assert validate_entry_name("null\0byte.txt") is not None


def test_safe_join_stays_inside_target(tmp_path: Path) -> None:
    target = tmp_path / "目标"
    target.mkdir()
    assert safe_join(target, "子目录/文件.txt") == target / "子目录" / "文件.txt"
    with pytest.raises(UnsafeArchiveError):
        safe_join(target, "../outside.txt")
    with pytest.raises(UnsafeArchiveError):
        safe_join(target, "C:/Windows/evil.dll")


def test_zip_slip_extraction_is_blocked(tmp_path: Path) -> None:
    archive = tmp_path / "恶意.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../evil.txt", "恶意内容")
        zf.writestr("正常文件.txt", "安全内容")
    engine = ZipEngine()
    out = tmp_path / "解压"
    with pytest.raises(UnsafeArchiveError) as exc:
        engine.extract(archive, ExtractOptions(target=out, policy=OverwritePolicy.OVERWRITE))
    assert "../../evil.txt" in exc.value.entries
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path.parent / "evil.txt").exists()


def test_absolute_path_zip_entry_is_blocked(tmp_path: Path) -> None:
    archive = tmp_path / "绝对路径.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("C:/Windows/System32/test.dll", "x")
    engine = ZipEngine()
    with pytest.raises(UnsafeArchiveError):
        engine.extract(archive, ExtractOptions(target=tmp_path / "out", policy=OverwritePolicy.OVERWRITE))


def test_unc_path_entry_is_blocked(tmp_path: Path) -> None:
    archive = tmp_path / "unc.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("\\\\server\\share\\evil.txt", "x")
    engine = ZipEngine()
    with pytest.raises(UnsafeArchiveError):
        engine.extract(archive, ExtractOptions(target=tmp_path / "out", policy=OverwritePolicy.OVERWRITE))


def test_tar_slip_is_blocked(tmp_path: Path) -> None:
    archive = tmp_path / "恶意.tar"
    payload = tmp_path / "payload.txt"
    payload.write_text("x", encoding="utf-8")
    with tarfile.open(archive, "w") as tar:
        tar.add(payload, arcname="../逃逸.txt")
    engine = TarEngine()
    with pytest.raises(UnsafeArchiveError):
        engine.extract(archive, ExtractOptions(target=tmp_path / "out", policy=OverwritePolicy.OVERWRITE))
    assert not (tmp_path / "逃逸.txt").exists()


def test_sevenzip_slip_is_blocked(tmp_path: Path) -> None:
    import py7zr
    import py7zr.py7zr as py7zr_module

    archive = tmp_path / "恶意.7z"
    source = tmp_path / "正常.txt"
    source.write_text("x", encoding="utf-8")
    # py7zr 自身会拒绝越界路径，这里临时放宽写入检查以构造恶意压缩包
    original_check = py7zr_module.check_archive_path
    py7zr_module.check_archive_path = lambda _path: True
    with py7zr.SevenZipFile(archive, "w") as zip_file:
        zip_file.writef(__import__("io").BytesIO(b"evil"), "../../evil.txt")
        zip_file.write(source, "正常.txt")
    py7zr_module.check_archive_path = original_check
    manager = ArchiveManager()
    with pytest.raises(UnsafeArchiveError):
        manager.extract_archive(
            archive, ExtractOptions(target=tmp_path / "out", policy=OverwritePolicy.OVERWRITE)
        )


def test_scan_archive_reports_danger(tmp_path: Path) -> None:
    archive = tmp_path / "恶意.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.txt", "x")
    manager = ArchiveManager()
    info = manager.list_archive(archive)
    issues = scan_archive(info)
    assert any(issue.level is IssueLevel.DANGER for issue in issues)
    assert any(issue.kind == "path_traversal" for issue in issues)


def test_zip_bomb_ratio_detected(tmp_path: Path) -> None:
    archive = tmp_path / "炸弹.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.txt", b"\0" * (20 * 1024 * 1024))
    manager = ArchiveManager()
    info = manager.list_archive(archive)
    assert info.total_size > 15 * 1024 * 1024
    limits = SafetyLimits(block_compression_ratio=100, warn_compression_ratio=10)
    issues = scan_archive(info, limits)
    assert any(issue.kind == "zip_bomb" for issue in issues)
    # 默认阈值下同样识别为高风险（20MB 全零数据压缩率极高）
    default_issues = scan_archive(info)
    assert any(issue.kind in {"zip_bomb", "size_limit"} for issue in default_issues)


def test_long_path_entry_is_reported(tmp_path: Path) -> None:
    name = "/".join(["长目录名称" + str(index) for index in range(40)]) + "/超长路径文件.txt"
    assert len(name) > 259
    archive = tmp_path / "长路径.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(name, "x")
    manager = ArchiveManager()
    info = manager.list_archive(archive)
    issues = scan_archive(info)
    assert any(issue.kind == "long_path" for issue in issues)


def test_symlink_entries_flagged_in_tar(tmp_path: Path) -> None:
    archive = tmp_path / "链接.tar"
    info = tarfile.TarInfo("link")
    info.type = tarfile.SYMTYPE
    info.linkname = "target"
    with tarfile.open(archive, "w") as tar:
        tar.addfile(info)
    manager = ArchiveManager()
    archive_info = manager.list_archive(archive)
    issues = scan_archive(archive_info)
    assert archive_info.has_symlinks
    assert any(issue.kind == "symlink" for issue in issues)


def test_disk_space_check_blocks_when_insufficient(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil
    from collections import namedtuple

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: Usage(100, 99, 1))
    report = check_disk_space(tmp_path, 10 * 1024 * 1024)
    assert not report.sufficient
    with pytest.raises(InsufficientSpaceError):
        require_disk_space(tmp_path, 10 * 1024 * 1024)


def test_extract_blocked_when_disk_space_insufficient(
    tmp_path: Path, sample_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil
    from collections import namedtuple

    Usage = namedtuple("Usage", "total used free")
    engine = ZipEngine()
    archive = tmp_path / "输出.zip"
    engine.create(CreateOptionsForTests(archive, sample_tree))
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: Usage(100, 99, 1))
    manager = ArchiveManager()
    with pytest.raises(InsufficientSpaceError):
        manager.extract_archive(
            archive, ExtractOptions(target=tmp_path / "解压", policy=OverwritePolicy.OVERWRITE)
        )
    assert not (tmp_path / "解压").exists()


def CreateOptionsForTests(target: Path, source: Path):
    from app.core.options import CreateOptions

    return CreateOptions(target=target, sources=[source])


def test_overwrite_never_default(tmp_path: Path, sample_tree: Path) -> None:
    """默认策略下不允许静默覆盖用户已有文件。"""

    engine = ZipEngine()
    archive = tmp_path / "输出.zip"
    engine.create(CreateOptionsForTests(archive, sample_tree))
    out = tmp_path / "解压"
    out.mkdir()
    existing = out  # 目标目录中已有内容
    (out / "测试项目").mkdir()
    marker = out / "测试项目" / "中文 文件夹"
    marker.mkdir(parents=True)
    target_file = marker / "测试文件.txt"
    target_file.write_text("用户原始数据", encoding="utf-8")

    with pytest.raises(UnsafeArchiveError):
        engine.extract(archive, ExtractOptions(target=existing, policy=OverwritePolicy.FAIL))
    assert target_file.read_text(encoding="utf-8") == "用户原始数据"
