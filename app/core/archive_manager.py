"""压缩包统一入口（Facade）：格式分发、分卷处理、安全校验与常用操作。"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.core.archive_info import ArchiveFormat, ArchiveInfo, strip_volume_suffix
from app.core.archive_security import (
    DiskSpaceReport,
    SecurityIssue,
    check_disk_space,
    require_disk_space,
    safe_join,
    scan_archive,
    validate_entry_name,
)
from app.core.engine_base import (
    ArchiveEngine,
    ArchiveSource,
    AskCallback,
    CancelCheck,
    ProgressCallback,
    never_cancelled,
    noop_progress,
    source_name,
)
from app.core.errors import (
    ArchiveCorruptedError,
    EntryNotFoundError,
    PackPilotError,
    UnsafeArchiveError,
    UnsupportedFormatError,
)
from app.core.options import (
    CreateOptions,
    ExtractOptions,
    ExtractResult,
    OverwritePolicy,
    SafetyLimits,
    TestReport,
)
from app.core.sevenzip_engine import SevenZipEngine
from app.core.tar_engine import TarEngine
from app.core.utils import ensure_directory, human_size
from app.core.volumes import (
    VolumeReader,
    VolumeSet,
    inspect_volume_set,
    is_volume_part,
    open_volume_reader,
    raise_if_incomplete,
    split_file,
    verify_volume_set,
    volume_base,
)
from app.core.zip_engine import ZipEngine

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OpenedArchive:
    """已打开的压缩包（可能是分卷集合）。"""

    path: Path
    format: ArchiveFormat
    source: ArchiveSource
    volume_set: VolumeSet | None = None
    reader: VolumeReader | None = None

    @property
    def name(self) -> str:
        return self.path.name

    def close(self) -> None:
        if self.reader is not None:
            with contextlib.suppress(Exception):
                self.reader.close()
            self.reader = None


@dataclass(slots=True)
class CreateResult:
    """创建压缩包的结果。"""

    target: Path
    volume_set: VolumeSet | None = None
    created_paths: list[Path] = field(default_factory=list)

    def describe(self) -> str:
        if self.volume_set is not None:
            return (
                f"已生成 {len(self.volume_set.parts)} 个分卷："
                f"{self.volume_set.parts[0].path.name} … "
                f"{self.volume_set.parts[-1].path.name}（共 {human_size(self.volume_set.total_size)}）"
            )
        return f"已生成：{self.target}"


class ArchiveManager:
    """统一管理所有压缩格式的操作入口。"""

    def __init__(self, *, limits: object = None, logger_: logging.Logger | None = None) -> None:
        self.limits: SafetyLimits | None = limits if isinstance(limits, SafetyLimits) else None
        self._engines: dict[ArchiveFormat, ArchiveEngine] = {}
        self._log = logger_ or logger

    # ------------------------------------------------------------ 格式与引擎
    @staticmethod
    def detect_format(path: Path | str) -> ArchiveFormat:
        detected = ArchiveFormat.from_path(Path(strip_volume_suffix(str(path))))
        if detected is None:
            raise UnsupportedFormatError(
                f"不支持的压缩格式：{Path(path).name}（支持 " + "、".join(ArchiveFormat.all_suffixes()) + "）"
            )
        return detected

    def engine_for(self, archive_format: ArchiveFormat) -> ArchiveEngine:
        engine = self._engines.get(archive_format)
        if engine is None:
            if archive_format is ArchiveFormat.ZIP:
                engine = ZipEngine()
            elif archive_format is ArchiveFormat.SEVEN_ZIP:
                engine = SevenZipEngine()
            elif archive_format.is_tar:
                engine = TarEngine(archive_format)
            else:  # pragma: no cover - 枚举已覆盖
                raise UnsupportedFormatError(f"没有可用的引擎：{archive_format}")
            self._engines[archive_format] = engine
        return engine

    def engine_for_path(self, path: Path | str) -> ArchiveEngine:
        return self.engine_for(self.detect_format(path))

    # ------------------------------------------------------------ 打开来源
    def volume_set_for(self, path: Path) -> VolumeSet | None:
        """返回分卷集合信息；``path`` 既可以是 ``.001`` 也可以是基准文件。"""

        path = Path(path)
        if is_volume_part(path):
            base = volume_base(path) or path
        else:
            base = path
            if base.exists() or not base.with_name(base.name + ".001").exists():
                return None
        volume_set = inspect_volume_set(base)
        if not volume_set.parts:
            return None
        return volume_set

    @contextlib.contextmanager
    def open(self, path: Path, *, verify_volumes: bool = False) -> Iterator[OpenedArchive]:
        """打开压缩包并返回可读取来源；分卷集合自动识别并校验。"""

        path = Path(path)
        archive_format = self.detect_format(path)
        volume_set = self.volume_set_for(path)
        if volume_set is None:
            if not path.exists():
                raise PackPilotError(f"文件不存在：{path}")
            opened = OpenedArchive(path=path, format=archive_format, source=path)
            try:
                yield opened
            finally:
                opened.close()
            return

        raise_if_incomplete(volume_set)
        reader = open_volume_reader(volume_set)
        opened = OpenedArchive(
            path=path, format=archive_format, source=reader, volume_set=volume_set, reader=reader
        )
        self._log.info("已打开分卷集合 %s（%d 卷）", path.name, len(volume_set.parts))
        try:
            yield opened
        finally:
            opened.close()

    def readable_source(self, path: Path) -> ArchiveSource:
        """返回可用于读取的来源（分卷时返回 :class:`VolumeReader`）。"""

        volume_set = self.volume_set_for(path)
        if volume_set is None:
            return Path(path)
        raise_if_incomplete(volume_set)
        return open_volume_reader(volume_set)

    # ------------------------------------------------------------ 基本信息
    def list_archive(self, path: Path, *, password: str | None = None) -> ArchiveInfo:
        with self.open(path) as opened:
            engine = self.engine_for(opened.format)
            info = engine.list_entries(opened.source, password=password)
            info.volumes = [part.path for part in opened.volume_set.parts] if opened.volume_set else []
            info.readable_path = path if isinstance(opened.source, Path) else None
            issues = scan_archive(info, self._limits())
            info.warnings = [*info.warnings, *(str(issue) for issue in issues)]
            return info

    def is_encrypted(self, path: Path) -> bool:
        path = Path(path)
        engine = self.engine_for_path(path)
        checker = getattr(engine, "is_encrypted", None)
        if checker is None:
            return False
        with self.open(path) as opened:
            return bool(checker(opened.source))

    def verify_password(self, path: Path, password: str) -> bool:
        try:
            self.list_archive(path, password=password)
        except PackPilotError:
            return False
        return True

    def security_scan(self, path: Path, *, password: str | None = None) -> list[SecurityIssue]:
        info = self.list_archive(path, password=password)
        return scan_archive(info, self._limits())

    def _limits(self) -> SafetyLimits:
        if self.limits is None:
            return SafetyLimits()
        return self.limits

    # ------------------------------------------------------------ 创建
    def create_archive(
        self,
        options: CreateOptions,
        *,
        progress: ProgressCallback = noop_progress,
        is_cancelled: CancelCheck = never_cancelled,
    ) -> CreateResult:
        archive_format = options.resolved_format()
        engine = self.engine_for(archive_format)
        target = engine.create(options, progress=progress, is_cancelled=is_cancelled)
        volume_set: VolumeSet | None = None
        created: list[Path] = [target]
        if options.volume_size:
            volume_set = split_file(
                target,
                part_size=options.volume_size,
                progress=progress,
                is_cancelled=is_cancelled,
                keep_original=False,
            )
            created = [part.path for part in volume_set.parts]
            created.append(volume_set.base.with_name(volume_set.base.name + ".packpilot-parts.json"))
        self._log.info("创建压缩包完成：%s", target)
        return CreateResult(target=target, volume_set=volume_set, created_paths=created)

    # ------------------------------------------------------------ 解压
    def estimate_extract_size(
        self, path: Path, entries: Sequence[str] | None = None, *, password: str | None = None
    ) -> int:
        info = self.list_archive(path, password=password)
        if not entries:
            return info.total_size
        selected = set(entries)
        return sum(
            entry.size
            for entry in info.entries
            if not entry.is_dir
            and (
                entry.name in selected
                or entry.name.rstrip("/") in {item.rstrip("/") for item in selected}
                or any(entry.name.startswith(item.rstrip("/") + "/") for item in selected)
            )
        )

    def check_space(self, target: Path, required_bytes: int, *, enforce: bool = True) -> DiskSpaceReport:
        if enforce:
            return require_disk_space(target, required_bytes)
        return check_disk_space(target, required_bytes)

    def extract_archive(
        self,
        path: Path,
        options: ExtractOptions,
        *,
        progress: ProgressCallback = noop_progress,
        is_cancelled: CancelCheck = never_cancelled,
        ask: AskCallback | None = None,
        password: str | None = None,
    ) -> ExtractResult:
        if password is not None and options.password is None:
            options.password = password
        info = self.list_archive(path, password=options.password)
        selected_names = options.entries
        if selected_names:
            known = {entry.name.rstrip("/") for entry in info.entries}
            missing = [name for name in selected_names if name.rstrip("/") not in known]
            if missing and not any(
                entry.name.rstrip("/").startswith(name.rstrip("/") + "/")
                for name in missing
                for entry in info.entries
            ):
                raise EntryNotFoundError("压缩包中不存在所选条目：" + "、".join(missing[:5]))

        unsafe = [
            entry.name
            for entry in info.entries
            if (selected_names is None or entry.name in set(selected_names))
            and validate_entry_name(entry.name, options.limits) is not None
        ]
        if unsafe:
            self._log.warning("拒绝解压：压缩包包含不安全路径 %s", unsafe[:5])
            raise UnsafeArchiveError(
                "压缩包包含越界路径，已阻止解压（Zip Slip 防护）",
                entries=unsafe,
                detail=validate_entry_name(unsafe[0], options.limits) or "",
            )

        required = self.estimate_extract_size(path, options.entries, password=options.password)
        if options.check_space:
            report = require_disk_space(options.target, required)
            self._log.info("磁盘空间检查通过：%s", report.message())
        ensure_directory(options.target)
        with self.open(path) as opened:
            engine = self.engine_for(opened.format)
            result = engine.extract(
                opened.source,
                options,
                progress=progress,
                is_cancelled=is_cancelled,
                ask=ask,
            )
        self._log.info("解压完成：%s → %s", path.name, options.target)
        return result

    def extract_entry_to_temp(
        self,
        path: Path,
        entry_name: str,
        temp_dir: Path,
        *,
        password: str | None = None,
        progress: ProgressCallback = noop_progress,
        is_cancelled: CancelCheck = never_cancelled,
    ) -> Path:
        """把单个条目解压到临时目录，返回临时文件路径（用于调用默认程序打开）。"""

        info = self.list_archive(path, password=password)
        entry_map = info.entry_map()
        key = entry_name.rstrip("/")
        entry = entry_map.get(key)
        if entry is None:
            raise EntryNotFoundError(f"压缩包中不存在条目：{entry_name}")
        if entry.is_dir:
            raise PackPilotError(f"{entry_name} 是目录，无法作为文件打开")
        ensure_directory(temp_dir)
        options = ExtractOptions(
            target=temp_dir,
            entries=[entry.name],
            password=password,
            policy=OverwritePolicy.OVERWRITE,
        )
        self.extract_archive(
            path,
            options,
            progress=progress,
            is_cancelled=is_cancelled,
        )
        return safe_join(temp_dir, entry.name)

    # ------------------------------------------------------------ 测试
    def test_archive(
        self,
        path: Path,
        *,
        password: str | None = None,
        progress: ProgressCallback = noop_progress,
        is_cancelled: CancelCheck = never_cancelled,
    ) -> TestReport:
        with self.open(path) as opened:
            engine = self.engine_for(opened.format)
            report = engine.test(
                opened.source,
                password=password,
                progress=progress,
                is_cancelled=is_cancelled,
            )
        self._log.info("测试完成：%s（%s）", path.name, "通过" if report.ok else "失败")
        return report

    # ------------------------------------------------------------ 修改
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
        self._require_plain_file(path)
        engine = self.engine_for_path(path)
        return engine.add_files(
            path,
            sources,
            level=level,
            password=password,
            progress=progress,
            is_cancelled=is_cancelled,
        )

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
        self._require_plain_file(path)
        engine = self.engine_for_path(path)
        return engine.delete_entries(
            path,
            names,
            password=password,
            progress=progress,
            is_cancelled=is_cancelled,
        )

    @staticmethod
    def _require_plain_file(path: Path) -> None:
        if is_volume_part(path):
            raise PackPilotError("分卷压缩包不支持直接修改条目，请先解压后重新压缩，或使用格式转换功能。")
        if not path.exists():
            raise PackPilotError(f"文件不存在：{path}")

    def verify_volumes(self, path: Path) -> VolumeSet:
        volume_set = self.volume_set_for(path)
        if volume_set is None:
            raise PackPilotError(f"未找到分卷文件：{Path(path).name}.001")
        return verify_volume_set(path)

    def readable_name(self, source: ArchiveSource) -> str:
        return source_name(source)

    def corrupted(self, path: Path, exc: Exception) -> ArchiveCorruptedError:
        return ArchiveCorruptedError(f"压缩包已损坏或无法解析：{Path(path).name}（{exc}）")
