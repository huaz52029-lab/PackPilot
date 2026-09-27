"""最近打开的压缩包记录（最多 N 条，默认 10 条）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.core.utils import atomic_write_json, read_json
from app.services import paths

logger = logging.getLogger(__name__)
MAX_RECORDS = 50


@dataclass(slots=True)
class RecentEntry:
    """一条最近使用记录。"""

    path: Path
    opened_at: datetime
    size: int = 0

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def display_name(self) -> str:
        return self.path.name

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "opened_at": self.opened_at.isoformat(timespec="seconds"),
            "size": self.size,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> RecentEntry | None:
        raw_path = payload.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            return None
        try:
            opened_at = datetime.fromisoformat(str(payload.get("opened_at") or ""))
        except ValueError:
            opened_at = datetime.now()
        size = payload.get("size")
        return cls(
            path=Path(raw_path),
            opened_at=opened_at,
            size=int(size) if isinstance(size, (int, float)) else 0,
        )


class RecentFilesService:
    """维护最近打开列表（去重、限长、原子写入）。"""

    def __init__(self, path: Path | None = None, *, limit: int = 10) -> None:
        self.path = Path(path) if path is not None else paths.recent_file()
        self.limit = max(1, min(MAX_RECORDS, limit))
        self._entries: list[RecentEntry] = self._load()

    def _load(self) -> list[RecentEntry]:
        payload = read_json(self.path, {"items": []})
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        entries: list[RecentEntry] = []
        for item in items:
            if isinstance(item, dict):
                entry = RecentEntry.from_dict(item)
                if entry is not None:
                    entries.append(entry)
        return entries[: self.limit]

    def save(self) -> None:
        payload = {"items": [entry.to_dict() for entry in self._entries[: self.limit]]}
        try:
            atomic_write_json(self.path, payload)
        except OSError as exc:  # pragma: no cover - 磁盘异常
            logger.error("保存最近使用记录失败：%s", exc)

    def add(self, path: Path, *, size: int | None = None) -> RecentEntry:
        path = Path(path)
        key = str(path).lower()
        self._entries = [entry for entry in self._entries if str(entry.path).lower() != key]
        if size is None:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
        entry = RecentEntry(path=path, opened_at=datetime.now(), size=size)
        self._entries.insert(0, entry)
        del self._entries[self.limit :]
        self.save()
        return entry

    def remove(self, path: Path) -> None:
        key = str(Path(path)).lower()
        self._entries = [entry for entry in self._entries if str(entry.path).lower() != key]
        self.save()

    def clear(self) -> None:
        self._entries = []
        self.save()

    def items(self) -> list[RecentEntry]:
        return list(self._entries)

    def existing(self) -> list[RecentEntry]:
        return [entry for entry in self._entries if entry.exists]
