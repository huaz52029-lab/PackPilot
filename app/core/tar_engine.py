"""TAR 系列引擎：基于标准库 :mod:`tarfile`（TAR / TAR.GZ / TAR.BZ2 / TAR.XZ）。"""

from __future__ import annotations

import contextlib
import os
import tarfile
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from app.core.archive_info import ArchiveEntry, ArchiveFormat, ArchiveInfo
from app.core.archive_security import (
    ConflictDecision,
    ensure_directory,
    ensure_entry_parent,
    resolve_conflict,
    safe_join,
    validate_entry_name,
)
from app.core.engine_base import (
    CHUNK_SIZE,
    ArchiveEngine,
    ArchiveSource,
    AskCallback,
    CancelCheck,
    CreateItem,
    ProgressCallback,
    ProgressReader,
    iter_create_items,
    never_cancelled,
    noop_progress,
    rewind_source,
    selection_set,
    source_as_path,
    source_mtime,
    source_name,
    source_size,
    unique_arcname,
)
from app.core.errors import (
    ArchiveCorruptedError,
    OperationNotSupportedError,
    TaskCancelledError,
    UnsafeArchiveError,
)
from app.core.options import CreateOptions, ExtractOptions, ExtractResult, TestReport
from app.core.utils import ensure_directory as ensure_dir_path
from app.core.utils import replace_file, unique_path


class TarEngine(ArchiveEngine):
    """TAR 系列格式实现（不提供密码能力）。"""

    def __init__(self, archive_format: ArchiveFormat = ArchiveFormat.TAR) -> None:
        if not archive_format.is_tar:
            raise ValueError(f"TarEngine 不支持格式：{archive_format}")
        self.format = archive_format

    @staticmethod
    def _open(source: ArchiveSource, mode: str = "r:*") -> tarfile.TarFile:
        if isinstance(source, (str, Path)):
            return tarfile.open(source, mode)
        rewind_source(source)
        return tarfile.open(fileobj=source, mode=mode)

    # ---------------------------------------------------------------- 读取
    def list_entries(self, source: ArchiveSource, *, password: str | None = None) -> ArchiveInfo:
        if password:
            raise OperationNotSupportedError(ArchiveFormat.TAR.password_note)
        entries: list[ArchiveEntry] = []
        try:
            with self._open(source) as archive:
                for member in archive.getmembers():
                    mtime: datetime | None = None
                    if member.mtime:
                        try:
                            mtime = datetime.fromtimestamp(member.mtime)
                        except (OverflowError, OSError, ValueError):
                            mtime = None
                    entries.append(
                        ArchiveEntry(
                            name=member.name,
                            is_dir=member.isdir(),
                            size=member.size if member.isreg() else 0,
                            compressed_size=None,
                            mtime=mtime,
                            crc=None,
                            is_symlink=member.issym(),
                            is_hardlink=member.islnk(),
                        )
                    )
        except (tarfile.ReadError, EOFError) as exc:
            raise ArchiveCorruptedError(
                f"无法读取 {self.format.display_name} 压缩包：{source_name(source)}（{exc}）"
            ) from exc
        except OSError as exc:
            raise ArchiveCorruptedError(f"读取 {source_name(source)} 时出错：{exc}") from exc

        warnings: list[str] = []
        if any(entry.is_symlink or entry.is_hardlink for entry in entries):
            warnings.append("压缩包包含符号链接/硬链接条目，解压时会被跳过")
        return ArchiveInfo(
            path=source_as_path(source) or Path(source_name(source)),
            format=self.format,
            entries=entries,
            size_bytes=source_size(source),
            mtime=source_mtime(source),
            warnings=warnings,
        )

    # ---------------------------------------------------------------- 创建
    def create(
        self,
        options: CreateOptions,
        *,
        progress: ProgressCallback = noop_progress,
        is_cancelled: CancelCheck = never_cancelled,
    ) -> Path:
        if options.resolved_format() != self.format:
            raise ValueError(f"TarEngine({self.format.value}) 无法创建 {options.resolved_format().value}")
        if options.password:
            raise OperationNotSupportedError(self.format.password_note)

        target = Path(options.target)
        ensure_dir_path(target.parent)
        if target.exists() and not options.replace_existing:
            target = unique_path(target)
        items = list(iter_create_items(options.sources, include_root=options.include_root))
        total = sum(item.size for item in items if not item.is_dir)
        done = 0
        tmp_path = target.with_name(f".{target.name}.packpilot-tmp")
        try:
            with tarfile.open(
                tmp_path,
                self.format.tar_mode,
                **self._open_kwargs(options.level),
                format=tarfile.PAX_FORMAT,
            ) as archive:
                archive.dereference = True
                if options.comment:
                    archive.pax_headers = {"comment": options.comment}
                for item in items:
                    if is_cancelled():
                        raise TaskCancelledError("压缩任务已取消")
                    if item.is_dir:
                        archive.addfile(archive.gettarinfo(str(item.fs_path), item.arcname))
                        progress(done, total, item.arcname)
                        continue
                    self._add_file(
                        archive,
                        item,
                        on_chunk=self._make_chunk_handler(item.arcname, progress, done, total),
                        is_cancelled=is_cancelled,
                    )
                    done += item.size
                    progress(done, total, item.arcname)
            replace_file(tmp_path, target)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        progress(total, total, target.name)
        return target

    def _compresslevel(self, level: object) -> int:
        if self.format is ArchiveFormat.TAR:
            return 6
        if self.format is ArchiveFormat.TAR_XZ:
            return level.xz_preset() if hasattr(level, "xz_preset") else 6
        if self.format is ArchiveFormat.TAR_BZ2:
            return level.bz2_level() if hasattr(level, "bz2_level") else 6
        return level.gzip_level() if hasattr(level, "gzip_level") else 6

    def _open_kwargs(self, level: object) -> dict[str, int]:
        """按压缩类型返回正确的压缩参数（xz 使用 preset，其它使用 compresslevel）。"""

        if self.format is ArchiveFormat.TAR:
            return {}
        if self.format is ArchiveFormat.TAR_XZ:
            preset = level.xz_preset() if hasattr(level, "xz_preset") else 6
            return {"preset": preset}
        return {"compresslevel": self._compresslevel(level)}

    @staticmethod
    def _make_chunk_handler(
        name: str,
        progress: ProgressCallback,
        base_done: int,
        total: int,
    ) -> Callable[[int], None]:
        state = {"done": 0}

        def handler(size: int) -> None:
            state["done"] += size
            progress(min(base_done + state["done"], total), total, name)

        return handler

    @staticmethod
    def _add_file(
        archive: tarfile.TarFile,
        item: CreateItem,
        *,
        on_chunk: Callable[[int], None],
        is_cancelled: CancelCheck,
    ) -> None:
        info = archive.gettarinfo(str(item.fs_path), item.arcname)
        if not info.isreg():
            archive.addfile(info)
            return
        with item.fs_path.open("rb") as handle:
            reader = ProgressReader(handle, on_chunk=on_chunk, is_cancelled=is_cancelled, name=item.arcname)
            archive.addfile(info, fileobj=reader)

    # ---------------------------------------------------------------- 解压
    def extract(
        self,
        source: ArchiveSource,
        options: ExtractOptions,
        *,
        progress: ProgressCallback = noop_progress,
        is_cancelled: CancelCheck = never_cancelled,
        ask: AskCallback | None = None,
    ) -> ExtractResult:
        target = Path(options.target)
        ensure_directory(target)
        result = ExtractResult(target=target)
        try:
            archive = self._open(source)
        except (tarfile.ReadError, EOFError) as exc:
            raise ArchiveCorruptedError(
                f"无法读取 {self.format.display_name} 压缩包：{source_name(source)}（{exc}）"
            ) from exc

        with archive:
            members = archive.getmembers()
            all_names = [member.name for member in members]
            selection = selection_set(all_names, options.entries, exact=options.exact_entries)
            unsafe = [
                name
                for name in all_names
                if (selection is None or name in selection)
                and validate_entry_name(name, options.limits) is not None
            ]
            if unsafe:
                raise UnsafeArchiveError(
                    "压缩包包含越界路径，已阻止解压（Zip Slip 防护）",
                    entries=unsafe,
                    detail=validate_entry_name(unsafe[0], options.limits) or "",
                )

            selected = [member for member in members if selection is None or member.name in selection]
            total = sum(member.size for member in selected if member.isreg())
            done = 0
            seen: set[str] = set()
            for member in selected:
                if is_cancelled():
                    raise TaskCancelledError("解压任务已取消")
                name = member.name
                if member.issym() or member.islnk() or member.ischr() or member.isblk() or member.isfifo():
                    result.skipped += 1
                    result.warnings.append(f"已跳过特殊条目（符号链接/硬链接/设备）：{name}")
                    progress(done, total, name)
                    continue
                destination = safe_join(target, name, options.limits)
                if member.isdir():
                    ensure_directory(destination)
                    result.extracted_directories += 1
                    progress(done, total, name)
                    continue
                if not member.isreg():
                    result.skipped += 1
                    progress(done, total, name)
                    continue
                ensure_entry_parent(destination)
                final_path, decision = resolve_conflict(
                    destination, options.policy, ask=ask, existing_paths=seen
                )
                if final_path is None:
                    result.skipped += 1
                    done += member.size
                    progress(done, total, name)
                    continue
                seen.add(str(final_path).lower())
                existed = final_path.exists()
                if decision is ConflictDecision.RENAME:
                    result.renamed += 1
                try:
                    source = archive.extractfile(member)
                    if source is None:
                        result.skipped += 1
                        result.warnings.append(f"无法读取条目内容：{name}")
                        continue
                    with source, final_path.open("wb") as dest:
                        while True:
                            if is_cancelled():
                                raise TaskCancelledError("解压任务已取消")
                            chunk = source.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            dest.write(chunk)
                            done += len(chunk)
                            progress(done, total, name)
                except TaskCancelledError:
                    if not existed:
                        final_path.unlink(missing_ok=True)
                    raise
                except (tarfile.TarError, OSError, EOFError) as exc:
                    if not existed:
                        final_path.unlink(missing_ok=True)
                    raise ArchiveCorruptedError(f"解压 {name} 失败：{exc}") from exc
                self._restore_metadata(final_path, member)
                result.extracted_files += 1
                result.bytes_written += member.size
                if not existed:
                    result.created_paths.append(final_path)
                progress(done, total, name)
        return result

    @staticmethod
    def _restore_metadata(path: Path, member: tarfile.TarInfo) -> None:
        if member.mtime:
            with contextlib.suppress(OSError, OverflowError, ValueError):
                os.utime(path, (member.mtime, member.mtime))
        if os.name == "posix" and member.mode:
            with contextlib.suppress(OSError):
                os.chmod(path, member.mode & 0o777)

    # ---------------------------------------------------------------- 测试
    def test(
        self,
        source: ArchiveSource,
        *,
        password: str | None = None,
        progress: ProgressCallback = noop_progress,
        is_cancelled: CancelCheck = never_cancelled,
    ) -> TestReport:
        started = time.monotonic()
        failures: list[str] = []
        notes: list[str] = []
        try:
            archive = self._open(source)
        except (tarfile.ReadError, EOFError) as exc:
            raise ArchiveCorruptedError(
                f"无法读取 {self.format.display_name} 压缩包：{source_name(source)}（{exc}）"
            ) from exc
        checked = 0
        total = 0
        with archive:
            members = archive.getmembers()
            total = len(members)
            for member in members:
                if is_cancelled():
                    raise TaskCancelledError("测试任务已取消")
                try:
                    if member.isreg():
                        source = archive.extractfile(member)
                        if source is None:
                            failures.append(member.name)
                            notes.append(f"{member.name}：无法读取条目内容")
                        else:
                            with source:
                                while True:
                                    if is_cancelled():
                                        raise TaskCancelledError("测试任务已取消")
                                    chunk = source.read(CHUNK_SIZE)
                                    if not chunk:
                                        break
                    elif not (member.isdir() or member.issym() or member.islnk()):
                        notes.append(f"{member.name}：跳过特殊条目校验")
                except TaskCancelledError:
                    raise
                except (tarfile.TarError, OSError, EOFError) as exc:
                    failures.append(member.name)
                    notes.append(f"{member.name}：{exc}")
                checked += 1
                progress(checked, total, member.name)
        if not failures:
            notes.append("文件结构正常")
            notes.append("压缩流完整性校验正常（TAR 本身不提供逐条目 CRC）")
        return TestReport(
            ok=not failures,
            checked=checked,
            total=total,
            failures=failures,
            notes=notes,
            elapsed=time.monotonic() - started,
        )

    # ---------------------------------------------------------------- 追加
    def add_files(
        self,
        path: Path,
        sources: Sequence[Path],
        *,
        level: object = None,
        password: str | None = None,
        progress: ProgressCallback = noop_progress,
        is_cancelled: CancelCheck = never_cancelled,
    ) -> list[str]:
        path = Path(path)
        info = self.list_entries(path)
        existing = {entry.name.rstrip("/").lower() for entry in info.entries}
        items = list(iter_create_items(sources))
        rename_map: dict[str, str] = {}
        for item in items:
            candidate = unique_arcname(item.arcname, existing)
            existing.add(candidate.rstrip("/").lower())
            rename_map[item.arcname] = candidate
        new_items = [
            CreateItem(item.fs_path, rename_map[item.arcname], item.is_dir, item.size) for item in items
        ]
        self._rewrite(
            path,
            keep=lambda member: True,
            extra_items=new_items,
            level=level,
            progress=progress,
            is_cancelled=is_cancelled,
            total_hint=info.total_size + sum(item.size for item in new_items if not item.is_dir),
        )
        return [item.arcname for item in new_items]

    # ---------------------------------------------------------------- 删除
    def delete_entries(
        self,
        path: Path,
        names: Sequence[str],
        *,
        password: str | None = None,
        progress: ProgressCallback = noop_progress,
        is_cancelled: CancelCheck = never_cancelled,
    ) -> int:
        path = Path(path)
        targets = {name.rstrip("/") for name in names}

        def keep(member: tarfile.TarInfo) -> bool:
            normalized = member.name.rstrip("/")
            return not any(normalized == target or normalized.startswith(target + "/") for target in targets)

        info = self.list_entries(path)
        removed = sum(1 for entry in info.entries if not keep(_FakeMember(entry.name)))
        if removed == 0:
            return 0
        self._rewrite(
            path,
            keep=keep,
            extra_items=[],
            level=None,
            progress=progress,
            is_cancelled=is_cancelled,
            total_hint=info.total_size,
        )
        return removed

    def _rewrite(
        self,
        path: Path,
        *,
        keep: Callable[[tarfile.TarInfo], bool],
        extra_items: Sequence[CreateItem],
        level: object,
        progress: ProgressCallback,
        is_cancelled: CancelCheck,
        total_hint: int,
    ) -> None:
        tmp_path = path.with_name(f".{path.name}.packpilot-tmp")
        done = 0
        total = max(total_hint, 0)
        try:
            with (
                tarfile.open(path, "r:*") as source,
                tarfile.open(
                    tmp_path,
                    self.format.tar_mode,
                    **self._open_kwargs(level if level is not None else 6),
                    format=tarfile.PAX_FORMAT,
                ) as target,
            ):
                for member in source.getmembers():
                    if is_cancelled():
                        raise TaskCancelledError("任务已取消")
                    if not keep(member):
                        continue
                    if member.isdir():
                        target.addfile(member)
                        continue
                    stream = source.extractfile(member)
                    if stream is None:
                        target.addfile(member)
                        continue
                    with stream:
                        target.addfile(
                            member,
                            fileobj=ProgressReader(
                                stream,
                                on_chunk=self._make_chunk_handler(member.name, progress, done, total),
                                is_cancelled=is_cancelled,
                                name=member.name,
                            ),
                        )
                    done += member.size
                    progress(done, total, member.name)
                for item in extra_items:
                    if is_cancelled():
                        raise TaskCancelledError("任务已取消")
                    if item.is_dir:
                        target.addfile(target.gettarinfo(str(item.fs_path), item.arcname))
                        continue
                    self._add_file(
                        target,
                        item,
                        on_chunk=self._make_chunk_handler(item.arcname, progress, done, total),
                        is_cancelled=is_cancelled,
                    )
                    done += item.size
                    progress(done, total, item.arcname)
            replace_file(tmp_path, path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise


class _FakeMember:
    """用于删除统计的轻量替身。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def isdir(self) -> bool:
        return False

    def issym(self) -> bool:
        return False

    def isreg(self) -> bool:
        return True
