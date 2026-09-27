"""临时文件管理：为“打开压缩包内文件”创建临时文件并确保及时清理。"""

from __future__ import annotations

import contextlib
import logging
import shutil
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from app.core.utils import atomic_write_json, read_json
from app.services import paths

logger = logging.getLogger(__name__)


class TempManager(QObject):
    """管理 PackPilot 创建的临时目录，并在文件解锁后删除。"""

    def __init__(self, root: Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.root = Path(root) if root is not None else paths.temp_root()
        self.open_root = self.root / "open"
        self.pending_file = self.root / "pending_cleanup.json"
        self._timers: dict[str, QTimer] = {}

    # ------------------------------------------------------------ 创建
    def create_workspace(self, prefix: str = "open") -> Path:
        """为一次“打开内部文件”创建独立临时目录。"""

        target = self.open_root / f"{prefix}-{uuid.uuid4().hex[:12]}"
        target.mkdir(parents=True, exist_ok=True)
        return target

    # ------------------------------------------------------------ 清理
    @staticmethod
    def _try_remove(path: Path) -> bool:
        if not path.exists():
            return True
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError:
            return False
        return True

    def schedule_cleanup(
        self,
        path: Path,
        *,
        initial_delay_ms: int = 2000,
        interval_ms: int = 3000,
        max_attempts: int = 400,
    ) -> None:
        """在文件被程序释放后删除临时目录（轮询尝试，超时后记录待清理）。"""

        path = Path(path)
        attempts = {"value": 0}
        timer = QTimer(self)
        timer.setInterval(max(500, interval_ms))

        def attempt() -> None:
            attempts["value"] += 1
            if self._try_remove(path):
                logger.info("已清理临时文件：%s", path)
                timer.stop()
                self._timers.pop(str(path), None)
                return
            if attempts["value"] >= max_attempts:
                logger.warning("临时文件仍被占用，将在下次启动时重试：%s", path)
                self._add_pending(path)
                timer.stop()
                self._timers.pop(str(path), None)

        timer.timeout.connect(attempt)
        self._timers[str(path)] = timer
        QTimer.singleShot(max(0, initial_delay_ms), attempt)
        timer.start()

    def cleanup_now(self, path: Path) -> bool:
        return self._try_remove(Path(path))

    def cleanup_all(self) -> int:
        """应用退出时清理全部临时目录，返回成功清理数量。"""

        removed = 0
        for timer in list(self._timers.values()):
            timer.stop()
        self._timers.clear()
        if self.open_root.exists():
            for child in self.open_root.iterdir():
                if self._try_remove(child):
                    removed += 1
                else:
                    self._add_pending(child)
        return removed

    def cleanup_stale(self, *, max_age_hours: int = 24) -> int:
        """清理历史残留临时目录与待清理列表。"""

        removed = 0
        if self.open_root.exists():
            now = time.time()
            for child in self.open_root.iterdir():
                try:
                    age_hours = (now - child.stat().st_mtime) / 3600
                except OSError:
                    continue
                if age_hours < max_age_hours:
                    continue
                if self._try_remove(child):
                    removed += 1
        for path in self._pending_paths():
            if self.cleanup_now(path):
                removed += 1
        self._write_pending([])
        return removed

    # ------------------------------------------------------------ 待清理列表
    def _pending_paths(self) -> list[Path]:
        payload = read_json(self.pending_file, {"items": []})
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        return [Path(str(item)) for item in items if isinstance(item, str) and item]

    def _add_pending(self, path: Path) -> None:
        items = {str(item) for item in self._pending_paths()}
        items.add(str(path))
        self._write_pending(sorted(items))

    def _write_pending(self, items: list[str]) -> None:
        with contextlib.suppress(OSError):
            atomic_write_json(self.pending_file, {"items": items})
