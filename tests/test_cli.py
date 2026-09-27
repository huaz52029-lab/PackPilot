"""CLI 测试：版本、压缩、解压、列表、测试、转换、哈希、分卷校验与错误码。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from app.cli.commands import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main, parse_size


def test_version_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == EXIT_OK
    output = capsys.readouterr().out
    assert "PackPilot 1.0.0" in output


def test_no_command_shows_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == EXIT_USAGE
    assert "compress" in capsys.readouterr().out


def test_parse_size_units() -> None:
    assert parse_size("100MB") == 100 * 1024 * 1024
    assert parse_size("1g") == 1024**3
    assert parse_size("2048") == 2048
    with pytest.raises(argparse.ArgumentTypeError):
        parse_size("abc")


def test_compress_list_test_extract(
    tmp_path: Path, sample_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = tmp_path / "命令行.zip"
    assert main(["compress", str(archive), str(sample_tree), "--level", "fastest"]) == EXIT_OK
    assert archive.exists()
    compress_output = capsys.readouterr().out
    assert "已生成" in compress_output

    assert main(["list", str(archive)]) == EXIT_OK
    list_output = capsys.readouterr().out
    assert "测试项目/中文 文件夹/测试文件.txt" in list_output

    assert main(["list", str(archive), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "ZIP"
    assert payload["file_count"] == 3
    assert any(entry["name"].endswith("测试文件.txt") for entry in payload["entries"])

    assert main(["test", str(archive)]) == EXIT_OK
    assert "测试通过" in capsys.readouterr().out

    assert main(["test", str(archive), "--json"]) == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True

    target = tmp_path / "解压目录"
    assert main(["extract", str(archive), str(target)]) == EXIT_OK
    assert (target / "测试项目" / "中文 文件夹" / "测试文件.txt").exists()
    assert "已解压" in capsys.readouterr().out


def test_extract_smart_default_directory(
    tmp_path: Path, sample_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = tmp_path / "智能.zip"
    assert main(["--quiet", "compress", str(archive), str(sample_tree)]) == EXIT_OK
    capsys.readouterr()
    assert main(["extract", str(archive)]) == EXIT_OK
    assert (tmp_path / "测试项目" / "中文 文件夹" / "测试文件.txt").exists()
    assert "智能解压" in capsys.readouterr().out


def test_convert_command(tmp_path: Path, sample_tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "源.zip"
    assert main(["--quiet", "compress", str(source), str(sample_tree)]) == EXIT_OK
    target = tmp_path / "目标.7z"
    assert main(["convert", str(source), str(target), "--level", "fast"]) == EXIT_OK
    assert target.exists() and source.exists()
    assert "已生成" in capsys.readouterr().out


def test_hash_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "文件.txt"
    path.write_text("PackPilot", encoding="utf-8")
    assert main(["hash", str(path), "--algorithm", "md5", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["algorithm"] == "MD5"
    assert len(payload[0]["digest"]) == 32

    export = tmp_path / "哈希.txt"
    assert main(["hash", str(path), "--algorithm", "sha256", "--output", str(export)]) == EXIT_OK
    assert export.exists()


def test_volume_commands(tmp_path: Path, sample_tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    archive = tmp_path / "分卷.zip"
    assert main(["compress", str(archive), str(sample_tree), "--volume-size", "400", "--quiet"]) == EXIT_OK
    first = archive.with_name(archive.name + ".001")
    assert first.exists()
    capsys.readouterr()

    assert main(["verify-volumes", str(first)]) == EXIT_OK
    assert "分卷完整" in capsys.readouterr().out

    assert main(["list", str(first), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["file_count"] == 3

    parts = sorted(tmp_path.glob("分卷.zip.0*"))
    backup = parts[1].read_bytes()
    parts[1].unlink()
    try:
        assert main(["verify-volumes", str(first)]) == EXIT_ERROR
        output = capsys.readouterr().out
        assert parts[1].name in output
    finally:
        parts[1].write_bytes(backup)


def test_missing_source_returns_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["compress", str(tmp_path / "输出.zip"), str(tmp_path / "不存在")]) == EXIT_ERROR
    assert "不存在" in capsys.readouterr().err


def test_unknown_extension_requires_format(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "a.txt"
    source.write_text("x", encoding="utf-8")
    assert main(["compress", str(tmp_path / "输出.rar"), str(source)]) == EXIT_USAGE
    assert "无法根据文件名判断格式" in capsys.readouterr().err


def test_corrupt_archive_returns_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    broken = tmp_path / "损坏.zip"
    broken.write_bytes("不是压缩包".encode())
    assert main(["test", str(broken)]) == EXIT_ERROR
    assert "错误" in capsys.readouterr().err


def test_associations_status_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["associations", "status"]) == EXIT_OK
    output = capsys.readouterr().out
    assert ".zip" in output
