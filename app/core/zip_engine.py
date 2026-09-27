"""ZIP 引擎：基于 Python 标准库 :mod:`zipfile`。"""

from __future__ import annotations

import contextlib
import os
import time
import zipfile
import zlib
from collections.abc import Sequence
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
    PackPilotError,
    PasswordRequiredError,
    TaskCancelledError,
    UnsafeArchiveError,
    UnsupportedFormatError,
    WrongPasswordError,
)
from app.core.options import CreateOptions, ExtractOptions, ExtractResult, TestReport
from app.core.utils import ensure_directory as ensure_dir_path
from app.core.utils import replace_file, unique_path


def _decode_comment(raw: bytes) -> str:
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp437", errors="replace")


def _to_datetime(date_time: tuple[int, int, int, int, int, int]) -> datetime | None:
    try:
        return datetime(*date_time)
    except (TypeError, ValueError):
        return None


def _password_bytes(password: str | None) -> bytes | None:
    return password.encode("utf-8") if password else None


def _describe_error(exc: BaseException) -> str:
    if isinstance(exc, NotImplementedError):
        return "不支持的 ZIP 加密方式（AES 加密 ZIP 需要 7-Zip 或专用工具）"
    if isinstance(exc, RuntimeError):
        text = str(exc)
        if "password" in text.lower():
            return "密码错误或未提供密码"
        return text
    if isinstance(exc, zipfile.BadZipFile):
        return f"数据校验失败：{exc}"
    return str(exc) or exc.__class__.__name__


class ZipEngine(ArchiveEngine):
    """ZIP 创建/解压/测试/追加/删除实现。"""

    format = ArchiveFormat.ZIP

    # ---------------------------------------------------------------- 读取
    def list_entries(self, source: ArchiveSource, *, password: str | None = None) -> ArchiveInfo:
        rewind_source(source)
        try:
            with zipfile.ZipFile(source) as archive:
                entries: list[ArchiveEntry] = []
                encrypted = False
                for info in archive.infolist():
                    entry_encrypted = bool(info.flag_bits & 0x1) or info.compress_type == 99
                    encrypted = encrypted or entry_encrypted
                    entries.append(
                        ArchiveEntry(
                            name=info.filename,
                            is_dir=info.is_dir(),
                            size=info.file_size,
                            compressed_size=info.compress_size,
                            mtime=_to_datetime(info.date_time),
                            crc=info.CRC,
                            is_encrypted=entry_encrypted,
                        )
                    )
                comment = _decode_comment(archive.comment)
        except zipfile.BadZipFile as exc:
            raise ArchiveCorruptedError(f"无法读取 ZIP 压缩包：{source_name(source)}（{exc}）") from exc
        except OSError as exc:
            raise PackPilotError(f"无法打开 ZIP 压缩包：{source_name(source)}（{exc}）") from exc

        warnings: list[str] = []
        if any(entry.ratio is not None and entry.ratio < 0.01 for entry in entries):
            warnings.append("部分条目压缩率极高，请确认来源可信")
        return ArchiveInfo(
            path=source_as_path(source) or Path(source_name(source)),
            format=ArchiveFormat.ZIP,
            entries=entries,
            comment=comment,
            encrypted=encrypted,
            size_bytes=source_size(source),
            mtime=source_mtime(source),
            warnings=warnings,
        )

    def is_encrypted(self, source: ArchiveSource) -> bool:
        return self.list_entries(source).encrypted

    # ---------------------------------------------------------------- 创建
    def create(
        self,
        options: CreateOptions,
        *,
        progress: ProgressCallback = noop_progress,
        is_cancelled: CancelCheck = never_cancelled,
    ) -> Path:
        if options.resolved_format() is not ArchiveFormat.ZIP:
            raise UnsupportedFormatError("ZipEngine 只能创建 ZIP 压缩包")
        if options.password:
            raise OperationNotSupportedError(ArchiveFormat.ZIP.password_note)

        target = Path(options.target)
        ensure_dir_path(target.parent)
        if target.exists() and not options.replace_existing:
            target = unique_path(target)
        items = list(iter_create_items(options.sources, include_root=options.include_root))
        total = sum(item.size for item in items if not item.is_dir)
        done = 0
        tmp_path = target.with_name(f".{target.name}.packpilot-tmp")
        compress_level = options.level.zip_level()
        try:
            with zipfile.ZipFile(
                tmp_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=compress_level,
                allowZip64=True,
                strict_timestamps=False,
            ) as archive:
                if options.comment:
                    archive.comment = options.comment.encode("utf-8")
                for item in items:
                    if is_cancelled():
                        raise TaskCancelledError("压缩任务已取消")
                    if item.is_dir:
                        archive.writestr(self._directory_info(item), b"")
                        progress(done, total, item.arcname)
                        continue
                    info = self._file_info(item)
                    try:
                        with item.fs_path.open("rb") as source, archive.open(info, "w") as dest:
                            while True:
                                if is_cancelled():
                                    raise TaskCancelledError("压缩任务已取消")
                                chunk = source.read(CHUNK_SIZE)
                                if not chunk:
                                    break
                                dest.write(chunk)
                                done += len(chunk)
                                progress(done, total, item.arcname)
                    except OSError as exc:
                        raise PackPilotError(f"读取文件失败：{item.fs_path}（{exc}）") from exc
            replace_file(tmp_path, target)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        progress(total, total, target.name)
        return target

    @staticmethod
    def _directory_info(item: CreateItem) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo.from_file(item.fs_path, item.arcname.rstrip("/") + "/")
        info.compress_type = zipfile.ZIP_STORED
        return info

    @staticmethod
    def _file_info(item: CreateItem) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo.from_file(item.fs_path, item.arcname)
        info.compress_type = zipfile.ZIP_DEFLATED
        return info

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
        pwd = _password_bytes(options.password)
        rewind_source(source)
        try:
            archive = zipfile.ZipFile(source)
        except zipfile.BadZipFile as exc:
            raise ArchiveCorruptedError(f"无法读取 ZIP 压缩包：{source_name(source)}（{exc}）") from exc

        with archive:
            infos = archive.infolist()
            all_names = [info.filename for info in infos]
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

            selected = [
                info
                for info in infos
                if selection is None or info.filename in selection or info.filename + "/" in selection
            ]
            total = sum(info.file_size for info in selected if not info.is_dir())
            done = 0
            seen: set[str] = set()
            for info in selected:
                if is_cancelled():
                    raise TaskCancelledError("解压任务已取消")
                name = info.filename
                destination = safe_join(target, name, options.limits)
                if info.is_dir():
                    ensure_directory(destination)
                    result.extracted_directories += 1
                    progress(done, total, name)
                    continue
                ensure_entry_parent(destination)
                final_path, decision = resolve_conflict(
                    destination, options.policy, ask=ask, existing_paths=seen
                )
                if final_path is None:
                    result.skipped += 1
                    done += info.file_size
                    progress(done, total, name)
                    continue
                seen.add(str(final_path).lower())
                existed = final_path.exists()
                if decision is ConflictDecision.RENAME:
                    result.renamed += 1
                try:
                    with archive.open(info, "r", pwd=pwd) as source, final_path.open("wb") as dest:
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
                except RuntimeError as exc:
                    if not existed:
                        final_path.unlink(missing_ok=True)
                    text = str(exc).lower()
                    if "password" in text:
                        if pwd:
                            raise WrongPasswordError(f"解压 {name} 失败：密码错误") from exc
                        raise PasswordRequiredError(f"解压 {name} 需要密码") from exc
                    raise PackPilotError(f"解压 {name} 失败：{exc}") from exc
                except NotImplementedError as exc:
                    if not existed:
                        final_path.unlink(missing_ok=True)
                    raise UnsupportedFormatError(
                        "该 ZIP 使用 AES 加密（压缩方式 99），Python 标准库无法解压，"
                        "请改用 7-Zip 或请作者使用 7Z 格式"
                    ) from exc
                except zipfile.BadZipFile as exc:
                    if not existed:
                        final_path.unlink(missing_ok=True)
                    raise ArchiveCorruptedError(f"解压 {name} 失败，数据已损坏：{exc}") from exc

                self._restore_mtime(final_path, info.date_time)
                result.extracted_files += 1
                result.bytes_written += info.file_size
                if not existed:
                    result.created_paths.append(final_path)
                progress(done, total, name)
        return result

    @staticmethod
    def _restore_mtime(path: Path, date_time: tuple[int, int, int, int, int, int]) -> None:
        moment = _to_datetime(date_time)
        if moment is None:
            return
        stamp = moment.timestamp()
        with contextlib.suppress(OSError):
            os.utime(path, (stamp, stamp))

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
        pwd = _password_bytes(password)
        rewind_source(source)
        try:
            archive = zipfile.ZipFile(source)
        except zipfile.BadZipFile as exc:
            raise ArchiveCorruptedError(f"无法读取 ZIP 压缩包：{source_name(source)}（{exc}）") from exc
        with archive:
            infos = archive.infolist()
            total = len(infos)
            checked = 0
            for info in infos:
                if is_cancelled():
                    raise TaskCancelledError("测试任务已取消")
                name = info.filename
                try:
                    if info.is_dir():
                        checked += 1
                        progress(checked, total, name)
                        continue
                    crc = 0
                    with archive.open(info, "r", pwd=pwd) as source:
                        while True:
                            if is_cancelled():
                                raise TaskCancelledError("测试任务已取消")
                            chunk = source.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            crc = zlib.crc32(chunk, crc)
                    if info.CRC is not None and not (info.flag_bits & 0x8) and crc != info.CRC:
                        failures.append(name)
                        notes.append(
                            f"{name}：CRC 校验失败（期望 {info.CRC:08X}，实际 {crc & 0xFFFFFFFF:08X}）"
                        )
                except TaskCancelledError:
                    raise
                except Exception as exc:
                    failures.append(name)
                    notes.append(f"{name}：{_describe_error(exc)}")
                checked += 1
                progress(checked, total, name)

        if not failures and not infos:
            notes.append("压缩包为空（不包含任何条目）")
        notes.append("文件结构正常")
        if not failures:
            notes.append("数据校验正常")
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
        total = sum(item.size for item in new_items if not item.is_dir) + info.total_size
        done = 0
        pwd = _password_bytes(password)
        compress_level = level.zip_level() if hasattr(level, "zip_level") else 6
        tmp_path = path.with_name(f".{path.name}.packpilot-tmp")
        try:
            with (
                zipfile.ZipFile(path) as source_archive,
                zipfile.ZipFile(
                    tmp_path,
                    "w",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=compress_level,
                    allowZip64=True,
                    strict_timestamps=False,
                ) as target_archive,
            ):
                for entry in source_archive.infolist():
                    if is_cancelled():
                        raise TaskCancelledError("添加文件任务已取消")
                    new_info = zipfile.ZipInfo(entry.filename, entry.date_time)
                    new_info.compress_type = entry.compress_type
                    new_info.external_attr = entry.external_attr
                    new_info.internal_attr = entry.internal_attr
                    new_info.create_system = entry.create_system
                    new_info.comment = entry.comment
                    new_info.flag_bits = entry.flag_bits & ~0x1
                    if entry.is_dir():
                        target_archive.writestr(new_info, b"")
                        continue
                    with (
                        source_archive.open(entry, "r", pwd=pwd) as src,
                        target_archive.open(new_info, "w") as dst,
                    ):
                        while True:
                            if is_cancelled():
                                raise TaskCancelledError("添加文件任务已取消")
                            chunk = src.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            dst.write(chunk)
                            done += len(chunk)
                            progress(done, total, entry.filename)
                for item in new_items:
                    if is_cancelled():
                        raise TaskCancelledError("添加文件任务已取消")
                    if item.is_dir:
                        target_archive.writestr(self._directory_info(item), b"")
                        continue
                    new_info = self._file_info(item)
                    with item.fs_path.open("rb") as src, target_archive.open(new_info, "w") as dst:
                        while True:
                            if is_cancelled():
                                raise TaskCancelledError("添加文件任务已取消")
                            chunk = src.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            dst.write(chunk)
                            done += len(chunk)
                            progress(done, total, item.arcname)
            replace_file(tmp_path, path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
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
        pwd = _password_bytes(password)
        tmp_path = path.with_name(f".{path.name}.packpilot-tmp")
        with zipfile.ZipFile(path) as source_archive:
            infos = source_archive.infolist()
            keep = [info for info in infos if not self._matches(info.filename, targets)]
            removed = len(infos) - len(keep)
            if removed == 0:
                return 0
            total = sum(info.file_size for info in keep if not info.is_dir())
            done = 0
            try:
                with zipfile.ZipFile(
                    tmp_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
                ) as target_archive:
                    for entry in keep:
                        if is_cancelled():
                            raise TaskCancelledError("删除条目任务已取消")
                        new_info = zipfile.ZipInfo(entry.filename, entry.date_time)
                        new_info.compress_type = entry.compress_type if entry.compress_type != 99 else 8
                        new_info.external_attr = entry.external_attr
                        new_info.internal_attr = entry.internal_attr
                        new_info.create_system = entry.create_system
                        if entry.is_dir():
                            target_archive.writestr(new_info, b"")
                            continue
                        with (
                            source_archive.open(entry, "r", pwd=pwd) as src,
                            target_archive.open(new_info, "w") as dst,
                        ):
                            while True:
                                if is_cancelled():
                                    raise TaskCancelledError("删除条目任务已取消")
                                chunk = src.read(CHUNK_SIZE)
                                if not chunk:
                                    break
                                dst.write(chunk)
                                done += len(chunk)
                                progress(done, total, entry.filename)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
        # 源压缩包句柄已关闭后再替换文件（Windows 不允许替换被占用的文件）
        replace_file(tmp_path, path)
        return removed

    @staticmethod
    def _matches(name: str, targets: set[str]) -> bool:
        normalized = name.rstrip("/")
        return any(normalized == target or normalized.startswith(target + "/") for target in targets)
