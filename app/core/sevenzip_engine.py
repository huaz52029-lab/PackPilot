"""7Z 引擎：基于 :mod:`py7zr`（支持 AES-256 密码、加密文件头、条目级 CRC 校验）。"""

from __future__ import annotations

import contextlib
import io
import lzma
import os
import threading
import time
import zlib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import py7zr
from py7zr.helpers import get_sanitized_output_path
from py7zr.io import Py7zIO, WriterFactory

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
    ArchiveEngine,
    ArchiveSource,
    AskCallback,
    CancelCheck,
    CreateItem,
    ProgressCallback,
    ProgressReader,
    iter_create_items,
    iter_tree_items,
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
    PackPilotError,
    PasswordRequiredError,
    TaskCancelledError,
    UnsafeArchiveError,
    UnsupportedFormatError,
    WrongPasswordError,
)
from app.core.options import (
    CreateOptions,
    ExtractOptions,
    ExtractResult,
    OverwritePolicy,
    TestReport,
)
from app.core.utils import ensure_directory as ensure_dir_path
from app.core.utils import replace_file, temporary_directory, unique_path


def _creation_time(value: object) -> datetime | None:
    if value is None:
        return None
    converter = getattr(value, "totimestamp", None)
    if callable(converter):
        try:
            return datetime.fromtimestamp(converter())
        except (OSError, OverflowError, ValueError):
            return None
    return None


class _DiscardWriter(Py7zIO):
    """丢弃数据的写入器（用于跳过条目或测试）。"""

    def __init__(self, on_write: object = None, on_close: object = None) -> None:
        self._on_write = on_write if callable(on_write) else None
        self._on_close = on_close if callable(on_close) else None
        self._size = 0

    def write(self, s: bytes | bytearray) -> int:
        self._size += len(s)
        if self._on_write is not None:
            self._on_write(len(s))
        return len(s)

    def read(self, size: int | None = None) -> bytes:
        raise io.UnsupportedOperation("read")

    def seek(self, offset: int, whence: int = 0) -> int:
        return offset

    def flush(self) -> None:
        return None

    def size(self) -> int:
        return self._size

    def close(self) -> None:
        if self._on_close is not None:
            self._on_close()


class _FileWriter(Py7zIO):
    """把 py7zr 的解压输出直接流式写入磁盘。"""

    def __init__(self, handle: io.BufferedWriter, on_write: object, is_cancelled: CancelCheck) -> None:
        self._handle = handle
        self._on_write = on_write if callable(on_write) else None
        self._is_cancelled = is_cancelled
        self._size = 0
        self._closed = False

    def write(self, s: bytes | bytearray) -> int:
        if self._is_cancelled():
            # 取消后丢弃数据，让 py7zr 尽快结束，任务层随后抛出取消异常
            return len(s)
        self._handle.write(bytes(s))
        self._size += len(s)
        if self._on_write is not None:
            self._on_write(len(s))
        return len(s)

    def read(self, size: int | None = None) -> bytes:
        raise io.UnsupportedOperation("read")

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._handle.seek(offset, whence)

    def flush(self) -> None:
        if not self._closed:
            self._handle.flush()

    def size(self) -> int:
        return self._size

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(OSError, ValueError):
            self._handle.flush()
        with contextlib.suppress(OSError, ValueError):
            self._handle.close()


class _ExtractWriterFactory(WriterFactory):
    """按条目名解析目标路径，并应用覆盖策略的解压写入工厂。"""

    def __init__(
        self,
        *,
        target: Path,
        entries: Sequence[ArchiveEntry],
        options: ExtractOptions,
        result: ExtractResult,
        progress: ProgressCallback,
        total_bytes: int,
        is_cancelled: CancelCheck,
        ask: AskCallback | None,
    ) -> None:
        self._target = target
        self._options = options
        self._result = result
        self._progress = progress
        self._total = total_bytes
        self._is_cancelled = is_cancelled
        self._ask = ask
        self._lock = threading.Lock()
        self._done = 0
        self._seen: set[str] = set()
        self.written: dict[str, Path] = {}
        self.skipped: list[str] = []
        self.writers: list[_FileWriter] = []
        self._key_map: dict[str, ArchiveEntry] = {}
        for entry in entries:
            try:
                key = get_sanitized_output_path(entry.name, target).as_posix()
            except Exception:
                continue
            self._key_map[key] = entry

    def create(self, filename: str) -> Py7zIO:
        entry = self._key_map.get(filename)
        name = entry.name if entry is not None else ""
        if not name:
            try:
                relative = Path(filename).resolve().relative_to(Path(self._target).resolve())
                name = relative.as_posix()
            except (ValueError, OSError):
                name = Path(filename).name
        with self._lock:
            destination = safe_join(self._target, name, self._options.limits)
            ensure_entry_parent(destination)
            final_path, decision = resolve_conflict(
                destination,
                self._options.policy,
                ask=self._ask,
                existing_paths=self._seen,
            )
            if final_path is None:
                self.skipped.append(name)
                return _DiscardWriter(
                    on_write=lambda size: self._report(size, name),
                    on_close=lambda: None,
                )
            if decision is ConflictDecision.RENAME:
                self._result.renamed += 1
            if decision is ConflictDecision.OVERWRITE:
                existed = final_path.exists()
                handle = self._open_for_write(final_path)
            else:
                existed = False
                handle = self._open_exclusive(final_path)
            self._seen.add(str(handle.name).lower())
            self.written[name] = Path(handle.name)
            if not existed:
                self._result.created_paths.append(Path(handle.name))

        def on_write(size: int) -> None:
            self._report(size, name)

        writer = _FileWriter(handle, on_write, self._is_cancelled)
        with self._lock:
            self.writers.append(writer)
        return writer

    @staticmethod
    def _open_for_write(path: Path) -> io.BufferedWriter:
        try:
            return path.open("wb")
        except OSError as exc:
            raise PackPilotError(f"无法写入文件：{path}（{exc}）") from exc

    def _open_exclusive(self, path: Path) -> io.BufferedWriter:
        try:
            return path.open("xb")
        except FileExistsError:
            candidate = unique_path(path)
            return candidate.open("xb")
        except OSError as exc:
            raise PackPilotError(f"无法写入文件：{path}（{exc}）") from exc

    def _report(self, size: int, name: str) -> None:
        with self._lock:
            self._done += size
            done = self._done
        self._progress(min(done, self._total), self._total, name)


class _TestWriterFactory(WriterFactory):
    """测试用写入工厂：逐文件校验 CRC，且不落盘。"""

    def __init__(
        self,
        *,
        crc_map: dict[str, int | None],
        failures: list[str],
        notes: list[str],
        on_file_done: object,
    ) -> None:
        self._crc_map = crc_map
        self._failures = failures
        self._notes = notes
        self._on_file_done = on_file_done if callable(on_file_done) else None
        self._lock = threading.Lock()

    def create(self, filename: str) -> Py7zIO:
        expected = self._crc_map.get(filename)
        return _VerifyingWriter(self, filename, expected)

    def finish(self, filename: str, expected: int | None, actual: int) -> None:
        with self._lock:
            if expected is not None and expected != actual:
                self._failures.append(filename)
                self._notes.append(f"{filename}：CRC 校验失败（期望 {expected:08X}，实际 {actual:08X}）")
            if self._on_file_done is not None:
                self._on_file_done(filename)


class _VerifyingWriter(Py7zIO):
    """只做 CRC 校验、不落盘的写入器。"""

    def __init__(self, factory: _TestWriterFactory, filename: str, expected: int | None) -> None:
        self._factory = factory
        self._filename = filename
        self._expected = expected
        self._crc = 0
        self._size = 0

    def write(self, s: bytes | bytearray) -> int:
        data = bytes(s)
        self._crc = zlib.crc32(data, self._crc)
        self._size += len(data)
        return len(data)

    def read(self, size: int | None = None) -> bytes:
        raise io.UnsupportedOperation("read")

    def seek(self, offset: int, whence: int = 0) -> int:
        return offset

    def flush(self) -> None:
        return None

    def size(self) -> int:
        return self._size

    def close(self) -> None:
        self._factory.finish(self._filename, self._expected, self._crc & 0xFFFFFFFF)


class SevenZipEngine(ArchiveEngine):
    """7Z 创建/解压/测试/追加/删除实现。"""

    format = ArchiveFormat.SEVEN_ZIP

    # ---------------------------------------------------------------- 读取
    def _open_read(self, source: ArchiveSource, password: str | None) -> py7zr.SevenZipFile:
        rewind_source(source)
        try:
            return py7zr.SevenZipFile(source, "r", password=password)
        except py7zr.exceptions.PasswordRequired as exc:
            raise PasswordRequiredError("该 7Z 压缩包已加密，请输入密码") from exc
        except py7zr.exceptions.Bad7zFile as exc:
            if password:
                raise WrongPasswordError("无法打开 7Z 压缩包：密码错误或文件已损坏") from exc
            raise ArchiveCorruptedError(f"无法读取 7Z 压缩包：{source_name(source)}（{exc}）") from exc
        except (lzma.LZMAError, py7zr.exceptions.CrcError) as exc:
            if password:
                raise WrongPasswordError("无法打开 7Z 压缩包：密码错误或文件已损坏") from exc
            raise ArchiveCorruptedError(f"无法读取 7Z 压缩包：{source_name(source)}（{exc}）") from exc
        except (TypeError, ValueError) as exc:
            # py7zr 在文件头被加密且未提供密码时可能抛出解析类异常（例如 Unknown field）
            if password:
                raise WrongPasswordError("无法打开 7Z 压缩包：密码错误或文件已损坏") from exc
            raise PasswordRequiredError(
                "无法解析 7Z 文件头：该压缩包可能已加密（需要密码），或文件已损坏"
            ) from exc
        except OSError as exc:
            raise PackPilotError(f"无法打开 7Z 压缩包：{source_name(source)}（{exc}）") from exc

    def is_encrypted(self, source: ArchiveSource) -> bool:
        try:
            with py7zr.SevenZipFile(source, "r") as archive:
                return bool(archive.needs_password())
        except py7zr.exceptions.PasswordRequired:
            return True
        except py7zr.exceptions.Bad7zFile as exc:
            raise ArchiveCorruptedError(f"无法读取 7Z 压缩包：{source_name(source)}（{exc}）") from exc

    def list_entries(self, source: ArchiveSource, *, password: str | None = None) -> ArchiveInfo:
        with self._open_read(source, password) as archive:
            file_infos = archive.list()
            encrypted = bool(archive.needs_password())
        entries: list[ArchiveEntry] = []
        for item in file_infos:
            is_dir = bool(item.is_directory)
            entries.append(
                ArchiveEntry(
                    name=item.filename,
                    is_dir=is_dir,
                    size=0 if is_dir else int(item.uncompressed or 0),
                    compressed_size=None if is_dir else (int(item.compressed) if item.compressed else None),
                    mtime=_creation_time(item.creationtime),
                    crc=int(item.crc32) if item.crc32 is not None else None,
                    is_encrypted=encrypted,
                    is_symlink=bool(getattr(item, "is_symlink", False)),
                )
            )
        warnings: list[str] = []
        if encrypted:
            warnings.append("该压缩包已加密，浏览与解压需要密码")
        if any(entry.is_symlink for entry in entries):
            warnings.append("压缩包包含符号链接条目，解压时会跳过")
        return ArchiveInfo(
            path=source_as_path(source) or Path(source_name(source)),
            format=ArchiveFormat.SEVEN_ZIP,
            entries=entries,
            encrypted=encrypted,
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
        if options.resolved_format() is not ArchiveFormat.SEVEN_ZIP:
            raise UnsupportedFormatError("SevenZipEngine 只能创建 7Z 压缩包")
        target = Path(options.target)
        ensure_dir_path(target.parent)
        if target.exists() and not options.replace_existing:
            target = unique_path(target)
        items = list(iter_create_items(options.sources, include_root=options.include_root))
        total = sum(item.size for item in items if not item.is_dir)
        done = 0
        tmp_path = target.with_name(f".{target.name}.packpilot-tmp")
        filters = [{"id": py7zr.FILTER_LZMA2, "preset": options.level.sevenzip_preset()}]
        try:
            with py7zr.SevenZipFile(
                tmp_path,
                "w",
                filters=filters,
                password=options.password,
                header_encryption=bool(options.password),
            ) as archive:
                for item in items:
                    if is_cancelled():
                        raise TaskCancelledError("压缩任务已取消")
                    if item.is_dir:
                        archive.write(item.fs_path, item.arcname)
                        progress(done, total, item.arcname)
                        continue
                    try:
                        with item.fs_path.open("rb") as handle:
                            reader = ProgressReader(
                                handle,
                                on_chunk=self._chunk_handler(item.arcname, progress, done, total),
                                is_cancelled=is_cancelled,
                                name=item.arcname,
                            )
                            archive.writef(reader, item.arcname)
                    except TaskCancelledError:
                        raise
                    except OSError as exc:
                        raise PackPilotError(f"读取文件失败：{item.fs_path}（{exc}）") from exc
                    done += item.size
                    progress(done, total, item.arcname)
            replace_file(tmp_path, target)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        progress(total, total, target.name)
        return target

    @staticmethod
    def _chunk_handler(name: str, progress: ProgressCallback, base_done: int, total: int) -> object:
        state = {"done": 0, "last": 0.0}

        def handler(size: int) -> None:
            state["done"] += size
            now = time.monotonic()
            if now - state["last"] < 0.1:
                return
            state["last"] = now
            progress(min(base_done + state["done"], total), total, name)

        return handler

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
        info = self.list_entries(source, password=options.password)
        selection = selection_set(info.names(), options.entries, exact=options.exact_entries)
        selected = [entry for entry in info.entries if selection is None or entry.name in selection]
        unsafe = [
            entry.name for entry in selected if validate_entry_name(entry.name, options.limits) is not None
        ]
        if unsafe:
            raise UnsafeArchiveError(
                "压缩包包含越界路径，已阻止解压（Zip Slip 防护）",
                entries=unsafe,
                detail=validate_entry_name(unsafe[0], options.limits) or "",
            )

        result = ExtractResult(target=target)
        directories = [entry for entry in selected if entry.is_dir]
        skipped_links = [entry for entry in selected if entry.is_symlink or entry.is_hardlink]
        skipped_names = {entry.name for entry in skipped_links}
        files = [entry for entry in selected if not entry.is_dir and entry.name not in skipped_names]
        for entry in directories:
            if is_cancelled():
                raise TaskCancelledError("解压任务已取消")
            ensure_directory(safe_join(target, entry.name, options.limits))
            result.extracted_directories += 1
        for entry in skipped_links:
            result.skipped += 1
            result.warnings.append(f"已跳过符号链接条目：{entry.name}")

        total = sum(entry.size for entry in files)
        progress(0, total, "准备解压")
        factory = _ExtractWriterFactory(
            target=target,
            entries=files,
            options=options,
            result=result,
            progress=progress,
            total_bytes=total,
            is_cancelled=is_cancelled,
            ask=ask,
        )
        archive = self._open_read(source, options.password)
        try:
            with archive:
                archive.extract(
                    path=target,
                    targets=[entry.name for entry in files],
                    factory=factory,
                )
        except py7zr.exceptions.PasswordRequired as exc:
            raise PasswordRequiredError("解压需要密码") from exc
        except (py7zr.exceptions.CrcError, lzma.LZMAError) as exc:
            if options.password:
                raise WrongPasswordError("解压失败：密码错误或数据已损坏") from exc
            raise ArchiveCorruptedError(f"解压失败：数据校验错误（{exc}）") from exc
        except py7zr.exceptions.DecompressionBombError as exc:
            raise PackPilotError(f"解压被安全限制阻止：{exc}") from exc
        except py7zr.exceptions.Bad7zFile as exc:
            raise ArchiveCorruptedError(f"解压失败：{exc}") from exc
        finally:
            for writer in factory.writers:
                with contextlib.suppress(Exception):
                    writer.close()

        if is_cancelled():
            for created in result.created_paths:
                created.unlink(missing_ok=True)
            raise TaskCancelledError("解压任务已取消")

        for entry in files:
            destination = factory.written.get(entry.name)
            if destination is None or entry.mtime is None:
                continue
            stamp = entry.mtime.timestamp()
            with contextlib.suppress(OSError):
                os.utime(destination, (stamp, stamp))
        result.extracted_files = len(factory.written)
        result.skipped += len(factory.skipped)
        result.bytes_written = sum(entry.size for entry in files if entry.name in factory.written)
        progress(total, total, target.name)
        return result

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
        info = self.list_entries(source, password=password)
        failures: list[str] = []
        notes: list[str] = []

        with self._open_read(source, password) as archive:
            structural = archive.test()
        if structural is False:
            notes.append("⚠ 压缩流摘要校验失败")
        elif structural is None:
            notes.append("压缩包未记录压缩流摘要，跳过该项校验")
        else:
            notes.append("压缩流摘要校验通过")

        file_entries = [entry for entry in info.entries if not entry.is_dir and not entry.is_symlink]
        crc_map: dict[str, int | None] = {}
        for entry in file_entries:
            try:
                key = get_sanitized_output_path(entry.name, None).as_posix()
            except Exception:
                key = entry.name
            crc_map[key] = entry.crc
        total = len(file_entries)
        checked = {"value": 0}

        def on_file_done(filename: str) -> None:
            checked["value"] += 1
            progress(checked["value"], total, filename)

        targets = [entry.name for entry in file_entries]
        attempts = 0
        with self._open_read(source, password) as archive:
            while targets and attempts < 32:
                if is_cancelled():
                    raise TaskCancelledError("测试任务已取消")
                factory = _TestWriterFactory(
                    crc_map=crc_map,
                    failures=failures,
                    notes=notes,
                    on_file_done=on_file_done,
                )
                before = len(failures)
                try:
                    archive.extract(targets=targets, factory=factory)
                    break
                except py7zr.exceptions.CrcError as exc:
                    name = str(exc.args[2]) if len(exc.args) > 2 else "未知条目"
                    if name not in failures:
                        failures.append(name)
                        notes.append(f"{name}：压缩数据 CRC 校验失败")
                    targets = [target for target in targets if target != name]
                except (lzma.LZMAError, py7zr.exceptions.DecompressionError) as exc:
                    if password:
                        raise WrongPasswordError("测试失败：密码错误或压缩包已损坏") from exc
                    raise ArchiveCorruptedError(f"测试失败：{exc}") from exc
                attempts += 1
                if len(failures) == before:
                    break
        if attempts >= 32:
            notes.append("损坏条目过多，已停止继续枚举失败条目")

        ok = not failures and structural is not False
        if ok:
            notes.append("文件结构正常")
            notes.append("数据校验正常")
        return TestReport(
            ok=ok,
            checked=min(checked["value"], total) if total else 0,
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
        info = self.list_entries(path, password=password)
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
            kept_entries=[entry.name for entry in info.entries],
            extra_items=new_items,
            level=level,
            password=password,
            progress=progress,
            is_cancelled=is_cancelled,
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
        info = self.list_entries(path, password=password)
        targets = {name.rstrip("/") for name in names}

        def removed_entry(entry: ArchiveEntry) -> bool:
            normalized = entry.name.rstrip("/")
            return any(normalized == target or normalized.startswith(target + "/") for target in targets)

        keep = [entry for entry in info.entries if not removed_entry(entry)]
        removed = len(info.entries) - len(keep)
        if removed == 0:
            return 0
        self._rewrite(
            path,
            kept_entries=[entry.name for entry in keep],
            extra_items=(),
            level=None,
            password=password,
            progress=progress,
            is_cancelled=is_cancelled,
        )
        return removed

    def _rewrite(
        self,
        path: Path,
        *,
        kept_entries: Sequence[str],
        extra_items: Sequence[CreateItem],
        level: object,
        password: str | None,
        progress: ProgressCallback,
        is_cancelled: CancelCheck,
    ) -> None:
        """把保留条目解压到临时目录后重新压缩（7Z 不支持原地修改）。"""

        preset = level.sevenzip_preset() if hasattr(level, "sevenzip_preset") else 5
        filters = [{"id": py7zr.FILTER_LZMA2, "preset": preset}]
        tmp_path = path.with_name(f".{path.name}.packpilot-tmp")
        with temporary_directory(prefix="packpilot-rewrite-") as workspace:
            extract_options = ExtractOptions(
                target=workspace,
                entries=list(kept_entries),
                password=password,
                policy=OverwritePolicy.OVERWRITE,
                exact_entries=True,
            )
            extract_result = self.extract(path, extract_options, progress=progress, is_cancelled=is_cancelled)
            kept_items = list(iter_tree_items(workspace))
            total = sum(item.size for item in kept_items if not item.is_dir) + sum(
                item.size for item in extra_items if not item.is_dir
            )
            done = 0
            try:
                with py7zr.SevenZipFile(
                    tmp_path,
                    "w",
                    filters=filters,
                    password=password,
                    header_encryption=bool(password),
                ) as archive:
                    for item in [*kept_items, *extra_items]:
                        if is_cancelled():
                            raise TaskCancelledError("任务已取消")
                        if item.is_dir:
                            archive.write(item.fs_path, item.arcname)
                            continue
                        with item.fs_path.open("rb") as handle:
                            reader = ProgressReader(
                                handle,
                                on_chunk=self._chunk_handler(item.arcname, progress, done, total),
                                is_cancelled=is_cancelled,
                                name=item.arcname,
                            )
                            archive.writef(reader, item.arcname)
                        done += item.size
                        progress(min(done, total), total, item.arcname)
                replace_file(tmp_path, path)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
            del extract_result
