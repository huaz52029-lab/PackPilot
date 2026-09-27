"""哈希测试：四种算法、批量、导出与取消。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from app.core.checksum import (
    ALGORITHMS,
    compare_digest,
    export_results,
    hash_file,
    hash_files,
    normalize_algorithm,
    results_to_text,
)
from app.core.errors import PackPilotError, TaskCancelledError


@pytest.mark.parametrize("algorithm", sorted(ALGORITHMS))
def test_hash_file_matches_hashlib(tmp_path: Path, algorithm: str) -> None:
    path = tmp_path / "数据.bin"
    payload = os.urandom(300_000)
    path.write_bytes(payload)
    result = hash_file(path, algorithm)
    expected = hashlib.new(algorithm, payload).hexdigest()
    assert result.digest == expected
    assert result.size == len(payload)
    assert result.ok


def test_hash_large_file(tmp_path: Path, large_file: Path) -> None:
    result = hash_file(large_file, "sha256")
    expected = hashlib.sha256(large_file.read_bytes()).hexdigest()
    assert result.digest == expected


def test_hash_multiple_files_and_text_export(tmp_path: Path) -> None:
    files = []
    for index in range(3):
        path = tmp_path / f"文件{index}.txt"
        path.write_text(f"内容 {index}", encoding="utf-8")
        files.append(path)
    progress_values: list[int] = []
    results = hash_files(files, "md5", progress=lambda done, total, _msg: progress_values.append(done))
    assert len(results) == 3
    assert progress_values == [1, 2, 3]
    text = results_to_text(results, source="单元测试")
    assert "MD5" in text and "单元测试" in text
    exported = export_results(results, tmp_path / "哈希.txt")
    assert exported.exists()
    assert "文件0.txt" in exported.read_text(encoding="utf-8")


def test_hash_cancel(tmp_path: Path, large_file: Path) -> None:
    calls = {"count": 0}

    def cancel_check() -> bool:
        calls["count"] += 1
        return calls["count"] > 3

    with pytest.raises(TaskCancelledError):
        hash_file(large_file, "sha512", is_cancelled=cancel_check)


def test_hash_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(PackPilotError):
        hash_file(tmp_path / "不存在.txt", "sha256")
    with pytest.raises(PackPilotError):
        hash_file(tmp_path, "sha256")


def test_normalize_algorithm_and_compare() -> None:
    assert normalize_algorithm("SHA-256") == "sha256"
    assert normalize_algorithm("sha1") == "sha1"
    with pytest.raises(PackPilotError):
        normalize_algorithm("crc32")
    assert compare_digest("ABCDEF", "abcdef")
    assert not compare_digest("abcd", "abce")


def test_hash_archive_file(tmp_path: Path, sample_tree: Path) -> None:
    from app.core.archive_manager import ArchiveManager
    from app.core.options import CreateOptions

    manager = ArchiveManager()
    archive = tmp_path / "输出.zip"
    manager.create_archive(CreateOptions(target=archive, sources=[sample_tree]))
    result = hash_file(archive, "sha256")
    assert result.digest == hashlib.sha256(archive.read_bytes()).hexdigest()
