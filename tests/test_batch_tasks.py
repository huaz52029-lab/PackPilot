"""批量压缩 / 批量解压任务测试（任务队列与逐项进度）。"""

from __future__ import annotations

import time
from pathlib import Path

from app.core.archive_info import ArchiveFormat
from app.core.archive_manager import ArchiveManager
from app.core.options import CompressionLevel, CreateOptions, ExtractOptions, OverwritePolicy
from app.tasks.base_task import TaskStatus
from app.tasks.compress_task import BatchCompressTask, MergeCompressTask
from app.tasks.extract_task import BatchExtractTask
from app.tasks.task_manager import TaskManager


def _wait(manager: TaskManager, task_id: str, qapp, timeout: float = 60.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        info = manager.info(task_id)
        if info is not None and info.status.is_final:
            qapp.processEvents()
            return
        time.sleep(0.01)
    raise AssertionError("批量任务未在超时时间内结束")


def _make_folders(tmp_path: Path) -> list[Path]:
    folders = []
    for name in ("项目A", "项目B", "项目C"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "文件.txt").write_text(f"{name} 内容", encoding="utf-8")
        (folder / "子目录").mkdir()
        (folder / "子目录" / "数据.bin").write_bytes(b"\x01\x02\x03" * 500)
        folders.append(folder)
    return folders


def test_batch_compress_each_folder_separate(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    folders = _make_folders(tmp_path)
    output_dir = tmp_path / "输出"
    output_dir.mkdir()
    manager = ArchiveManager()
    task_manager = TaskManager(max_threads=2)
    template = CreateOptions(
        target=output_dir / "占位.zip",
        sources=folders,
        format=ArchiveFormat.ZIP,
        level=CompressionLevel.FASTEST,
    )
    task_id = task_manager.submit(
        BatchCompressTask(manager, folders, target_dir=output_dir, template=template)
    )
    _wait(task_manager, task_id, qapp)
    info = task_manager.info(task_id)
    assert info is not None and info.status is TaskStatus.COMPLETED
    for folder in folders:
        archive = output_dir / f"{folder.name}.zip"
        assert archive.exists(), f"缺少 {archive.name}"
        listed = manager.list_archive(archive)
        assert listed.file_count == 2
        assert any(folder.name in name for name in listed.names())


def test_batch_compress_merge_into_single_archive(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    folders = _make_folders(tmp_path)
    manager = ArchiveManager()
    task_manager = TaskManager(max_threads=1)
    target = tmp_path / "合并.zip"
    options = CreateOptions(
        target=target, sources=folders, format=ArchiveFormat.ZIP, level=CompressionLevel.FASTEST
    )
    task_id = task_manager.submit(MergeCompressTask(manager, options))
    _wait(task_manager, task_id, qapp)
    assert target.exists()
    info = manager.list_archive(target)
    assert info.file_count == 6
    assert {name.split("/")[0] for name in info.names()} == {"项目A", "项目B", "项目C"}


def test_batch_extract_multiple_archives(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    folders = _make_folders(tmp_path)
    manager = ArchiveManager()
    archives: list[Path] = []
    for folder in folders:
        archive = tmp_path / f"{folder.name}.zip"
        manager.create_archive(CreateOptions(target=archive, sources=[folder]))
        archives.append(archive)
    # 追加一个 7Z 与一个 TAR.GZ，验证混合格式批量解压
    seven_zip = tmp_path / "项目D.7z"
    manager.create_archive(
        CreateOptions(target=seven_zip, sources=[folders[0]], format=ArchiveFormat.SEVEN_ZIP)
    )
    tar_gz = tmp_path / "项目E.tar.gz"
    manager.create_archive(CreateOptions(target=tar_gz, sources=[folders[1]], format=ArchiveFormat.TAR_GZ))
    archives.extend([seven_zip, tar_gz])

    output_dir = tmp_path / "解压输出"
    task_manager = TaskManager(max_threads=2)
    task_id = task_manager.submit(
        BatchExtractTask(
            manager,
            archives,
            target_dir=output_dir,
            template=ExtractOptions(target=output_dir, policy=OverwritePolicy.RENAME),
            use_stem_subdir=True,
        )
    )
    _wait(task_manager, task_id, qapp)
    info = task_manager.info(task_id)
    assert info is not None and info.status is TaskStatus.COMPLETED
    assert "5 个压缩包" in info.message
    assert (output_dir / "项目A" / "项目A" / "文件.txt").exists()
    assert (output_dir / "项目E" / "项目B" / "文件.txt").exists()


def test_batch_extract_reports_progress_per_archive(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    folder = _make_folders(tmp_path)[0]
    manager = ArchiveManager()
    archives = []
    for index in range(3):
        archive = tmp_path / f"批量{index}.zip"
        manager.create_archive(CreateOptions(target=archive, sources=[folder]))
        archives.append(archive)
    output_dir = tmp_path / "输出"
    task_manager = TaskManager(max_threads=1)
    messages: list[str] = []

    def record(_task_id: str, info: object) -> None:
        current = getattr(info, "current_file", "") or ""
        message = getattr(info, "message", "") or ""
        if current:
            messages.append(current)
        if message:
            messages.append(message)

    task_manager.task_updated.connect(record)
    task_id = task_manager.submit(
        BatchExtractTask(
            manager,
            archives,
            target_dir=output_dir,
            template=ExtractOptions(target=output_dir, policy=OverwritePolicy.RENAME),
        )
    )
    _wait(task_manager, task_id, qapp)
    # 等待排队中的信号全部送达（工作线程发信号 → 主线程事件队列）
    for _ in range(50):
        qapp.processEvents()
        time.sleep(0.005)
    final = task_manager.info(task_id)
    assert final is not None and "3 个压缩包" in final.message
    assert any("[1/3]" in message for message in messages)
    assert any("[3/3]" in message for message in messages)
