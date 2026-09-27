"""日志配置：输出到 ``%LOCALAPPDATA%\\PackPilot\\logs``（滚动文件）。"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from app.services import paths
from app.version import VERSION_DISPLAY

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_configured = False


def setup_logging(level: int = logging.INFO, *, log_dir: Path | None = None) -> Path:
    """初始化日志系统，返回日志文件路径。"""

    global _configured
    target_dir = Path(log_dir) if log_dir is not None else paths.logs_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    log_path = target_dir / "packpilot.log"

    root = logging.getLogger()
    root.setLevel(level)
    if _configured:
        return log_path

    formatter = logging.Formatter(LOG_FORMAT)
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if not getattr(sys, "frozen", False):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.setLevel(logging.WARNING)
        root.addHandler(console)

    logging.getLogger(__name__).info("%s 启动，日志文件：%s", VERSION_DISPLAY, log_path)
    _configured = True
    return log_path


def install_exception_hook(*, fatal_dialog: bool = True) -> None:
    """安装全局异常钩子，把未捕获异常写入日志。"""

    logger = logging.getLogger("packpilot.crash")

    def hook(exc_type, exc_value, exc_traceback) -> None:  # type: ignore[no-untyped-def]
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical("未捕获异常", exc_info=(exc_type, exc_value, exc_traceback))
        if fatal_dialog:
            try:
                from PySide6.QtWidgets import QApplication, QMessageBox

                if QApplication.instance() is not None:
                    QMessageBox.critical(
                        None,
                        "PackPilot 发生错误",
                        f"{exc_type.__name__}: {exc_value}\n\n详细信息已写入日志文件。",
                    )
            except Exception:  # pragma: no cover - 弹窗失败时忽略
                pass

    sys.excepthook = hook
