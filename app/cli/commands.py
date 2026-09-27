"""PackPilot 命令行接口。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from app.core.archive_info import ArchiveFormat
from app.core.archive_manager import ArchiveManager
from app.core.checksum import ALGORITHMS, export_results, hash_files, normalize_algorithm
from app.core.converter import ArchiveConverter
from app.core.errors import PackPilotError, TaskCancelledError
from app.core.options import (
    CompressionLevel,
    ConvertOptions,
    CreateOptions,
    ExtractOptions,
    OverwritePolicy,
)
from app.core.utils import human_duration, human_size
from app.core.volumes import verify_volume_set
from app.version import VERSION_DISPLAY

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_CANCELLED = 3

_SIZE_UNITS = {"k": 1024, "kb": 1024, "m": 1024**2, "mb": 1024**2, "g": 1024**3, "gb": 1024**3}


def parse_size(text: str) -> int:
    """解析 ``100MB`` / ``1G`` / ``1048576`` 形式的大小。"""

    cleaned = text.strip().lower().replace(" ", "")
    if not cleaned:
        raise argparse.ArgumentTypeError("分卷大小不能为空")
    for suffix, multiplier in sorted(_SIZE_UNITS.items(), key=lambda item: -len(item[0])):
        if cleaned.endswith(suffix):
            number = cleaned[: -len(suffix)]
            try:
                return int(float(number) * multiplier)
            except ValueError as exc:
                raise argparse.ArgumentTypeError(f"无法解析分卷大小：{text}") from exc
    try:
        return int(cleaned)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"无法解析分卷大小：{text}") from exc


def _stream_out():  # type: ignore[no-untyped-def]
    return sys.stdout if sys.stdout is not None else sys.stderr


def _print(message: str = "") -> None:
    stream = _stream_out()
    if stream is not None:
        stream.write(message + "\n")
        stream.flush()


def _print_error(message: str) -> None:
    stream = sys.stderr if sys.stderr is not None else sys.stdout
    if stream is not None:
        stream.write(f"错误：{message}\n")
        stream.flush()


class ProgressPrinter:
    """命令行进度输出（同一行刷新）。"""

    def __init__(self, *, enabled: bool = True, label: str = "") -> None:
        self.enabled = enabled
        self.label = label
        self._last_percent = -1
        self._last_time = 0.0

    def __call__(self, done: int, total: int, message: str = "") -> None:
        if not self.enabled:
            return
        percent = int(done * 100 / total) if total else 0
        now = time.monotonic()
        if percent == self._last_percent and now - self._last_time < 0.2:
            return
        self._last_percent = percent
        self._last_time = now
        stream = _stream_out()
        if stream is None:
            return
        stream.write(f"\r{self.label} {percent:3d}%  {message[:60]:<60}")
        stream.flush()

    def finish(self) -> None:
        if not self.enabled:
            return
        stream = _stream_out()
        if stream is not None:
            stream.write("\n")
            stream.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="packpilot",
        description="PackPilot —— Windows 轻量级压缩包管理器命令行工具",
    )
    parser.add_argument("--version", "-V", action="store_true", help="显示版本号并退出")
    parser.add_argument("--quiet", "-q", action="store_true", help="不输出进度信息")
    subparsers = parser.add_subparsers(dest="command", metavar="命令")

    compress = subparsers.add_parser("compress", help="创建压缩包")
    compress.add_argument("target", help="输出压缩包路径（扩展名决定格式）")
    compress.add_argument("sources", nargs="+", help="要压缩的文件或文件夹")
    compress.add_argument("--format", choices=[item.value for item in ArchiveFormat], default=None)
    compress.add_argument(
        "--level",
        choices=[item.value for item in CompressionLevel],
        default=CompressionLevel.NORMAL.value,
    )
    compress.add_argument("--password", default=None, help="密码（仅 7Z 支持）")
    compress.add_argument("--volume-size", type=parse_size, default=None, help="分卷大小，如 100MB")

    extract = subparsers.add_parser("extract", help="解压压缩包")
    extract.add_argument("archive", help="压缩包路径（分卷可指定 .001）")
    extract.add_argument("target", nargs="?", default=None, help="目标目录，默认使用智能解压")
    extract.add_argument("--password", default=None)
    extract.add_argument(
        "--on-conflict",
        choices=[policy.value for policy in OverwritePolicy if policy is not OverwritePolicy.ASK],
        default=OverwritePolicy.RENAME.value,
    )
    extract.add_argument("--entries", default=None, help="仅解压指定条目（逗号分隔）")

    listing = subparsers.add_parser("list", help="列出压缩包内容")
    listing.add_argument("archive")
    listing.add_argument("--password", default=None)
    listing.add_argument("--json", action="store_true", help="以 JSON 输出")

    test = subparsers.add_parser("test", help="测试压缩包完整性")
    test.add_argument("archive")
    test.add_argument("--password", default=None)
    test.add_argument("--json", action="store_true")

    convert = subparsers.add_parser("convert", help="转换压缩格式")
    convert.add_argument("source")
    convert.add_argument("target")
    convert.add_argument(
        "--level",
        choices=[item.value for item in CompressionLevel],
        default=CompressionLevel.NORMAL.value,
    )
    convert.add_argument("--password", default=None, help="源压缩包密码")
    convert.add_argument("--target-password", default=None, help="目标压缩包密码（仅 7Z）")
    convert.add_argument("--volume-size", type=parse_size, default=None)

    hash_command = subparsers.add_parser("hash", help="计算文件或压缩包哈希")
    hash_command.add_argument("files", nargs="+")
    hash_command.add_argument("--algorithm", choices=sorted(ALGORITHMS), default="sha256", help="哈希算法")
    hash_command.add_argument("--json", action="store_true")
    hash_command.add_argument("--output", default=None, help="导出到 TXT 文件")

    volumes = subparsers.add_parser("verify-volumes", help="校验分卷完整性")
    volumes.add_argument("archive", help="分卷文件（.001）或基准名")
    volumes.add_argument("--json", action="store_true")

    associations = subparsers.add_parser("associations", help="管理 Windows 文件关联与右键菜单")
    associations.add_argument("action", choices=["install", "remove", "status"])
    associations.add_argument("--no-default", action="store_true", help="不修改默认打开方式")
    return parser


def _cmd_compress(args: argparse.Namespace, manager: ArchiveManager) -> int:
    target = Path(args.target)
    archive_format = ArchiveFormat(args.format) if args.format else ArchiveFormat.from_path(target)
    if archive_format is None:
        _print_error(f"无法根据文件名判断格式：{target.name}，请使用 --format 指定")
        return EXIT_USAGE
    sources = [Path(item) for item in args.sources]
    missing = [str(item) for item in sources if not item.exists()]
    if missing:
        _print_error("以下路径不存在：" + "、".join(missing))
        return EXIT_ERROR
    options = CreateOptions(
        target=target,
        sources=sources,
        format=archive_format,
        level=CompressionLevel(args.level),
        password=args.password,
        volume_size=args.volume_size,
    )
    printer = ProgressPrinter(enabled=not args.quiet, label="压缩")
    result = manager.create_archive(options, progress=printer)
    printer.finish()
    _print(result.describe())
    return EXIT_OK


def _cmd_extract(args: argparse.Namespace, manager: ArchiveManager) -> int:
    archive = Path(args.archive)
    if not archive.exists() and not archive.with_name(archive.name + ".001").exists():
        _print_error(f"压缩包不存在：{archive}")
        return EXIT_ERROR
    info = manager.list_archive(archive, password=args.password)
    if args.target:
        target = Path(args.target)
    else:
        from app.core.smart_extract import plan_smart_extract

        plan = plan_smart_extract(archive, info)
        target = plan.directory
        if not args.quiet:
            _print(f"智能解压：{plan.reason}")
    entries = args.entries.split(",") if args.entries else None
    options = ExtractOptions(
        target=target,
        entries=entries,
        password=args.password,
        policy=OverwritePolicy(args.on_conflict),
    )
    printer = ProgressPrinter(enabled=not args.quiet, label="解压")
    result = manager.extract_archive(archive, options, progress=printer)
    printer.finish()
    _print(result.summary())
    _print(f"目标目录：{result.target}")
    for warning in result.warnings[:10]:
        _print(f"提示：{warning}")
    return EXIT_OK


def _cmd_list(args: argparse.Namespace, manager: ArchiveManager) -> int:
    info = manager.list_archive(Path(args.archive), password=args.password)
    if args.json:
        payload = {
            "path": str(info.path),
            "format": info.format.display_name,
            "encrypted": info.encrypted,
            "volumes": [str(item) for item in info.volumes],
            "file_count": info.file_count,
            "dir_count": info.dir_count,
            "total_size": info.total_size,
            "total_compressed_size": info.total_compressed_size,
            "entries": [
                {
                    "name": entry.name,
                    "is_dir": entry.is_dir,
                    "size": entry.size,
                    "compressed_size": entry.compressed_size,
                    "mtime": entry.mtime.isoformat() if entry.mtime else None,
                    "crc32": entry.crc,
                    "encrypted": entry.is_encrypted,
                }
                for entry in info.entries
            ],
        }
        _print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK
    _print(f"压缩包：{info.path}")
    _print(f"格式：{info.format.display_name}    加密：{'是' if info.encrypted else '否'}")
    _print(
        f"文件：{info.file_count}    目录：{info.dir_count}    "
        f"原始大小：{human_size(info.total_size)}    压缩后：{human_size(info.total_compressed_size)}"
    )
    if info.is_volume_set:
        _print(f"分卷：{len(info.volumes)} 卷")
    _print("-" * 78)
    _print(f"{'类型':<6}{'大小':>12}  {'压缩后':>12}  名称")
    for entry in info.entries:
        kind = "目录" if entry.is_dir else "文件"
        compressed = "-" if entry.compressed_size is None else human_size(entry.compressed_size)
        _print(f"{kind:<6}{human_size(entry.size):>12}  {compressed:>12}  {entry.name}")
    return EXIT_OK


def _cmd_test(args: argparse.Namespace, manager: ArchiveManager) -> int:
    # JSON 输出必须保持机器可读，因此不输出进度
    printer = ProgressPrinter(enabled=not args.quiet and not args.json, label="测试")
    report = manager.test_archive(Path(args.archive), password=args.password, progress=printer)
    printer.finish()
    if args.json:
        payload = {
            "ok": report.ok,
            "checked": report.checked,
            "total": report.total,
            "failures": report.failures,
            "notes": report.notes,
            "elapsed": round(report.elapsed, 3),
        }
        _print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK if report.ok else EXIT_ERROR
    _print(report.summary())
    for note in report.notes:
        _print(f"  {note}")
    if report.failures:
        _print("失败条目：")
        for name in report.failures[:50]:
            _print(f"  - {name}")
    return EXIT_OK if report.ok else EXIT_ERROR


def _cmd_convert(args: argparse.Namespace, manager: ArchiveManager) -> int:
    converter = ArchiveConverter(manager)
    options = ConvertOptions(
        source=Path(args.source),
        target=Path(args.target),
        level=CompressionLevel(args.level),
        source_password=args.password,
        target_password=args.target_password,
        volume_size=args.volume_size,
    )
    printer = ProgressPrinter(enabled=not args.quiet, label="转换")
    result = converter.convert(options, progress=printer)
    printer.finish()
    _print(result.describe())
    return EXIT_OK


def _cmd_hash(args: argparse.Namespace, manager: ArchiveManager) -> int:
    algorithm = normalize_algorithm(args.algorithm)
    printer = ProgressPrinter(enabled=not args.quiet and not args.json, label="哈希")
    started = time.monotonic()
    results = hash_files([Path(item) for item in args.files], algorithm, progress=printer)
    printer.finish()
    if args.output:
        export_results(results, Path(args.output))
        _print(f"已导出：{args.output}")
    if args.json:
        payload = [
            {
                "path": str(result.path),
                "algorithm": result.algorithm_label,
                "digest": result.digest,
                "size": result.size,
                "error": result.error,
            }
            for result in results
        ]
        _print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for result in results:
            if result.ok:
                _print(f"{result.digest}  {result.algorithm_label}  {result.path}")
            else:
                _print_error(f"{result.path}：{result.error}")
        _print(f"耗时：{human_duration(time.monotonic() - started)}")
    return EXIT_OK if all(result.ok for result in results) else EXIT_ERROR


def _cmd_verify_volumes(args: argparse.Namespace, manager: ArchiveManager) -> int:
    printer = ProgressPrinter(enabled=not args.quiet and not args.json, label="校验分卷")
    volume_set = verify_volume_set(Path(args.archive), progress=printer)
    printer.finish()
    if args.json:
        payload = {
            "base": str(volume_set.base),
            "parts": [
                {"name": part.path.name, "size": part.size, "sha256": part.sha256}
                for part in volume_set.parts
            ],
            "missing": volume_set.missing,
            "problems": volume_set.problems,
            "complete": volume_set.complete,
        }
        _print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print(volume_set.describe())
        for part in volume_set.parts:
            _print(f"  {part.path.name}  {human_size(part.size)}  {part.sha256 or ''}")
        for problem in volume_set.problems:
            _print(f"问题：{problem}")
        if volume_set.missing:
            _print("缺失分卷：" + "、".join(volume_set.missing))
    return EXIT_OK if volume_set.complete else EXIT_ERROR


def _cmd_associations(args: argparse.Namespace) -> int:
    from app.windows import context_menu, file_association

    if args.action == "install":
        registered = file_association.install_file_associations(set_default=not args.no_default)
        if not registered:
            _print_error("当前系统不是 Windows，无法写入注册表")
            return EXIT_ERROR
        context_menu.install_context_menu()
        _print("已注册扩展名：" + "、".join(registered))
        _print("右键菜单：" + context_menu.context_menu_summary())
        return EXIT_OK
    if args.action == "remove":
        file_association.remove_file_associations()
        context_menu.remove_context_menu()
        _print("已移除 PackPilot 文件关联与右键菜单")
        return EXIT_OK
    status = file_association.association_status()
    for suffix, ok in status.items():
        _print(f"{suffix:<10}{'已关联' if ok else '未关联'}")
    _print(context_menu.context_menu_summary())
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """CLI 入口，返回进程退出码。"""

    parser = build_parser()
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    # 允许 --quiet 出现在任意位置（argparse 的全局选项在子命令前才生效）
    quiet = False
    filtered: list[str] = []
    seen_separator = False
    for token in raw_arguments:
        if token == "--":
            seen_separator = True
            filtered.append(token)
            continue
        if not seen_separator and token in {"--quiet", "-q"}:
            quiet = True
            continue
        filtered.append(token)
    args = parser.parse_args(filtered)
    args.quiet = quiet or getattr(args, "quiet", False)
    if args.version:
        _print(VERSION_DISPLAY)
        return EXIT_OK
    if not args.command:
        parser.print_help()
        return EXIT_USAGE
    try:
        if args.command == "associations":
            return _cmd_associations(args)
        manager = ArchiveManager()
        handlers = {
            "compress": _cmd_compress,
            "extract": _cmd_extract,
            "list": _cmd_list,
            "test": _cmd_test,
            "convert": _cmd_convert,
            "hash": _cmd_hash,
            "verify-volumes": _cmd_verify_volumes,
        }
        return handlers[args.command](args, manager)
    except TaskCancelledError as exc:
        _print_error(str(exc))
        return EXIT_CANCELLED
    except PackPilotError as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    except KeyboardInterrupt:
        _print_error("操作已中断")
        return EXIT_CANCELLED


if __name__ == "__main__":  # pragma: no cover - 直接运行模块时
    raise SystemExit(main())
