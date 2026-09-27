"""PackPilot 统一入口：GUI、CLI 与 Windows 资源管理器快速操作。"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import sys
from pathlib import Path

from app.version import VERSION_DISPLAY

logger = logging.getLogger(__name__)

QUICK_ACTIONS = (
    "--quick-compress",
    "--quick-compress-ask",
    "--quick-extract",
    "--quick-extract-to",
    "--quick-browse",
    "--quick-test",
)

INTEGRATION_FLAGS = {
    "--install-associations",
    "--remove-associations",
    "--install-context-menu",
    "--remove-context-menu",
    "--association-status",
}


def run_integration_flag(flag: str) -> int:
    """执行 Windows 集成相关的命令行参数（安装程序/卸载程序调用）。"""

    from app.windows import context_menu, file_association

    printed = False
    if flag == "--install-associations":
        registered = file_association.install_file_associations()
        context_menu.install_context_menu()
        logger.info("已注册文件关联：%s", registered)
        return 0
    if flag == "--remove-associations":
        file_association.remove_file_associations()
        logger.info("已移除文件关联")
        return 0
    if flag == "--install-context-menu":
        context_menu.install_context_menu()
        logger.info("已安装右键菜单")
        return 0
    if flag == "--remove-context-menu":
        context_menu.remove_context_menu()
        logger.info("已移除右键菜单")
        return 0
    if flag == "--association-status":
        status = file_association.association_status()
        stream = sys.stdout or sys.stderr
        if stream is not None:
            for suffix, registered_flag in status.items():
                stream.write(f"{suffix}\t{'已关联' if registered_flag else '未关联'}\n")
            stream.write(f"右键菜单：{context_menu.context_menu_summary()}\n")
            stream.flush()
        printed = True
    del printed
    return 0


def _attach_console() -> bool:
    """Windows GUI 模式下附加到父进程控制台，使 ``PackPilot.exe --version`` 有输出。"""

    if os.name != "nt":
        return sys.stdout is not None
    attached = False
    with contextlib.suppress(Exception):
        ATTACH_PARENT_PROCESS = -1
        attached = bool(ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS))  # type: ignore[attr-defined]
    if not attached:
        # 若父进程没有控制台，则尝试新建一个，保证 CLI 输出可见
        with contextlib.suppress(Exception):
            attached = bool(ctypes.windll.kernel32.AllocConsole())  # type: ignore[attr-defined]
    if not attached:
        return False
    # 控制台代码页切换为 UTF-8，保证中文输出不乱码
    with contextlib.suppress(Exception):
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # type: ignore[attr-defined]
        ctypes.windll.kernel32.SetConsoleCP(65001)  # type: ignore[attr-defined]
    try:
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1, errors="replace")  # noqa: SIM115
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1, errors="replace")  # noqa: SIM115
        return True
    except OSError:
        return False


def _ensure_std_streams() -> None:
    """确保在 GUI 子系统下 ``sys.stdout`` / ``sys.stderr`` 可用。"""

    _force_utf8_streams()
    if sys.stdout is not None and sys.stderr is not None:
        return
    # 1) 若进程已被重定向（管道/文件），直接复用继承来的标准句柄
    if sys.stdout is None:
        stream = _stream_from_std_handle(-11)
        sys.stdout = stream if stream is not None else None
    if sys.stderr is None:
        stream = _stream_from_std_handle(-12)
        sys.stderr = stream if stream is not None else None
    # 2) 交互式控制台：附加到父进程控制台
    if sys.stdout is None or sys.stderr is None:
        _attach_console()
    # 3) 兜底：丢弃输出，避免写入 None 崩溃
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def _force_utf8_streams() -> None:
    """把标准输出/错误流切换为 UTF-8，避免中文在管道与重定向时乱码。"""

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with contextlib.suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")
        elif getattr(stream, "encoding", "").lower() not in {"utf-8", "utf8"}:
            with contextlib.suppress(Exception):
                setattr(
                    sys,
                    name,
                    open(  # noqa: SIM115 - 需要长期持有标准流句柄
                        stream.fileno(),
                        "w",
                        encoding="utf-8",
                        buffering=1,
                        errors="replace",
                        closefd=False,
                    ),
                )


def _stream_from_std_handle(which: int):  # type: ignore[no-untyped-def]
    """把继承的 Windows 标准句柄包装为文本流（支持管道与文件重定向）。"""

    if os.name != "nt":
        return None
    try:
        import msvcrt

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(which)
        if not handle or handle == ctypes.c_void_p(-1).value:
            return None
        if kernel32.GetFileType(handle) == 0:  # FILE_TYPE_UNKNOWN
            return None
        if kernel32.GetFileType(handle) == 2:  # FILE_TYPE_CHAR：真实控制台
            with contextlib.suppress(Exception):
                kernel32.SetConsoleOutputCP(65001)
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_TEXT)  # type: ignore[attr-defined]
    except (OSError, ValueError, AttributeError):
        return None
    try:
        return open(fd, "w", encoding="utf-8", buffering=1, errors="replace")
    except OSError:
        return None


def parse_quick_action(argv: list[str]) -> tuple[str, list[str]] | None:
    """解析资源管理器右键菜单传入的参数。"""

    if not argv:
        return None
    action = argv[0]
    if action not in QUICK_ACTIONS:
        return None
    return action, argv[1:]


def run_cli(argv: list[str]) -> int:
    from app.cli.commands import main as cli_main

    return cli_main(argv)


def run_selftest() -> int:
    """自检：验证当前环境（含打包后的 EXE）能否导入 Qt、创建主窗口与核心组件。

    该命令不显示窗口，适合安装后验证与 CI 检查。
    """

    from app.services.logger import setup_logging

    log_path = setup_logging()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    results: list[tuple[str, bool, str]] = []

    def check(name: str, func: object) -> None:
        try:
            detail = func()  # type: ignore[operator]
            results.append((name, True, str(detail)))
        except Exception as exc:
            logger.exception("自检项失败：%s", name)
            results.append((name, False, f"{exc.__class__.__name__}: {exc}"))

    from app.core.archive_manager import ArchiveManager
    from app.core.checksum import hash_file
    from app.core.tar_engine import TarEngine
    from app.core.zip_engine import ZipEngine

    check("核心层导入", lambda: "ArchiveManager / 引擎可用")
    check("ZIP 引擎", lambda: ZipEngine().format.display_name)
    check("TAR 引擎", lambda: TarEngine().format.display_name)

    def check_py7zr() -> str:
        from app.core.sevenzip_engine import SevenZipEngine

        return SevenZipEngine().format.display_name

    check("7Z 引擎（py7zr）", check_py7zr)
    check("哈希模块", lambda: hash_file.__name__)
    check("压缩包门面", lambda: ArchiveManager() and "可实例化")

    application = None

    def check_qt() -> str:
        nonlocal application
        from PySide6.QtCore import qVersion
        from PySide6.QtWidgets import QApplication

        application = QApplication.instance() or QApplication(["PackPilot", "--selftest"])
        return f"Qt {qVersion()}"

    check("Qt / PySide6", check_qt)

    def check_window() -> str:
        from app.gui.main_window import MainWindow
        from app.gui.theme import apply_theme

        window = MainWindow()
        if application is not None:
            apply_theme(application, window.settings.settings.theme)
        title = window.windowTitle()
        window.close()
        return title

    check("主窗口构建", check_window)

    def check_integration() -> str:
        from app.windows import context_menu, file_association

        return file_association.association_summary() + "；" + context_menu.context_menu_summary()

    check("Windows 集成模块", check_integration)

    stream = _stream_out_safe()
    if stream is not None:
        from app.version import VERSION_DISPLAY

        stream.write(f"{VERSION_DISPLAY} 自检结果：\n")
        for name, ok, detail in results:
            stream.write(f"  [{'通过' if ok else '失败'}] {name}：{detail}\n")
        stream.write(f"  日志文件：{log_path}\n")
        stream.flush()
    failed = [name for name, ok, _detail in results if not ok]
    return 1 if failed else 0


def _stream_out_safe():  # type: ignore[no-untyped-def]
    if sys.stdout is None:
        _ensure_std_streams()
    return sys.stdout if sys.stdout is not None else sys.stderr


def run_gui(argv: list[str] | None = None) -> int:
    """启动图形界面；``argv`` 可包含快速操作参数。"""

    from PySide6.QtWidgets import QApplication

    from app.gui.main_window import MainWindow
    from app.gui.theme import apply_theme
    from app.services.logger import install_exception_hook, setup_logging

    setup_logging()
    install_exception_hook()
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("PackPilot")
    application.setApplicationDisplayName(VERSION_DISPLAY)
    from app.version import APP_ID, ORGANIZATION, __version__

    application.setApplicationVersion(__version__)
    application.setOrganizationName(ORGANIZATION)
    application.setDesktopFileName(APP_ID)

    window = MainWindow()
    apply_theme(application, window.settings.settings.theme)
    window.show()
    if argv:
        _apply_quick_actions(window, argv)
    return application.exec()


def _apply_quick_actions(window, argv: list[str]) -> None:  # type: ignore[no-untyped-def]
    """执行资源管理器右键菜单请求的操作（真实执行，非模拟）。"""

    parsed = parse_quick_action(argv)
    if parsed is None:
        return
    action, parameters = parsed

    from app.core.options import CompressionLevel, CreateOptions

    try:
        if action in {"--quick-browse", "--quick-extract-to", "--quick-test"}:
            if not parameters:
                return
            path = Path(parameters[0])
            from PySide6.QtCore import QTimer

            if action == "--quick-browse":
                QTimer.singleShot(0, lambda: window.open_archive(path))
                return

            def after_open() -> None:
                if window.current_archive is None:
                    return
                if action == "--quick-extract-to":
                    window.extract_entries(None)
                else:
                    window.test_archive()

            window.archive_opened.connect(lambda _path: after_open())
            QTimer.singleShot(0, lambda: window.open_archive(path))
            return
        if action == "--quick-compress":
            if len(parameters) < 2:
                return
            source = Path(parameters[0])
            fmt = parameters[1].lower()
            from app.core.archive_info import ArchiveFormat

            archive_format = {
                "zip": ArchiveFormat.ZIP,
                "7z": ArchiveFormat.SEVEN_ZIP,
                "tar": ArchiveFormat.TAR,
                "tar.gz": ArchiveFormat.TAR_GZ,
            }.get(fmt, ArchiveFormat.ZIP)
            name = source.stem if source.is_file() else source.name
            target = source.with_name(f"{name}{archive_format.default_suffix}")
            if target.resolve() == source.resolve():
                target = source.with_name(f"{name}-packed{archive_format.default_suffix}")
            options = CreateOptions(
                target=target,
                sources=[source],
                format=archive_format,
                level=CompressionLevel(window.settings.default_level),
            )
            window._submit_compress(options)
            return
        if action == "--quick-compress-ask":
            if not parameters:
                return
            source = Path(parameters[0])
            window.new_archive(sources=[source])
            return
        if action == "--quick-extract":
            if not parameters:
                return
            from PySide6.QtCore import QTimer

            path = Path(parameters[0])

            def extract_here() -> None:
                window.extract_to_current_folder()

            window.archive_opened.connect(lambda _path: extract_here())
            QTimer.singleShot(0, lambda: window.open_archive(path))
            return
    except Exception as exc:
        from PySide6.QtWidgets import QMessageBox

        logger.exception("快速操作失败")
        QMessageBox.critical(window, "操作失败", f"{exc}")


def main(argv: list[str] | None = None) -> int:
    """程序入口：无参数启动 GUI，有参数走 CLI，快速操作参数启动 GUI 并执行操作。"""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        _ensure_std_streams()
        if arguments[0] in {"--version", "-V"}:
            stream = sys.stdout
            if stream is not None:
                stream.write(VERSION_DISPLAY + "\n")
                stream.flush()
            return 0
        if arguments[0] in INTEGRATION_FLAGS:
            from app.services.logger import setup_logging

            setup_logging()
            return run_integration_flag(arguments[0])
        if arguments[0] == "--selftest":
            return run_selftest()
        if arguments[0] in {"--help", "-h"}:
            return run_cli(arguments)
        if parse_quick_action(arguments) is not None:
            return run_gui(arguments)
        if arguments[0] == "--gui":
            return run_gui(arguments[1:])
        return run_cli(arguments)
    return run_gui([])


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
