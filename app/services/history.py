"""任务历史记录（``%APPDATA%\\PackPilot\\history.json``）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.core.utils import atomic_write_json, read_json
from app.services import paths

logger = logging.getLogger(__name__)
MAX_RECORDS = 1000


@dataclass(slots=True)
class HistoryRecord:
    """一条任务历史。"""

    timestamp: str
    kind: str
    title: str
    source: str = ""
    target: str = ""
    status: str = ""
    duration: float = 0.0
    error: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "kind": self.kind,
            "title": self.title,
            "source": self.source,
            "target": self.target,
            "status": self.status,
            "duration": round(self.duration, 3),
            "error": self.error,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> HistoryRecord:
        return cls(
            timestamp=str(payload.get("timestamp") or ""),
            kind=str(payload.get("kind") or ""),
            title=str(payload.get("title") or ""),
            source=str(payload.get("source") or ""),
            target=str(payload.get("target") or ""),
            status=str(payload.get("status") or ""),
            duration=float(payload.get("duration") or 0.0),
            error=str(payload.get("error") or ""),
            detail=str(payload.get("detail") or ""),
        )


class HistoryService:
    """保存与查询任务历史。"""

    def __init__(self, path: Path | None = None, *, limit: int = 500) -> None:
        self.path = Path(path) if path is not None else paths.history_file()
        self.limit = max(1, min(MAX_RECORDS, limit))
        self._records: list[HistoryRecord] = self._load()

    def _load(self) -> list[HistoryRecord]:
        payload = read_json(self.path, {"items": []})
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        return [HistoryRecord.from_dict(item) for item in items if isinstance(item, dict)]

    def save(self) -> None:
        payload = {"items": [record.to_dict() for record in self._records[: self.limit]]}
        try:
            atomic_write_json(self.path, payload)
        except OSError as exc:  # pragma: no cover - 磁盘异常
            logger.error("保存任务历史失败：%s", exc)

    def add(self, record: HistoryRecord) -> HistoryRecord:
        self._records.insert(0, record)
        del self._records[self.limit :]
        self.save()
        return record

    def records(self, limit: int | None = None) -> list[HistoryRecord]:
        if limit is None:
            return list(self._records)
        return list(self._records[:limit])

    def clear(self) -> None:
        self._records = []
        self.save()

    def export(self, target: Path) -> Path:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = ["时间\t任务类型\t标题\t源\t目标\t结果\t耗时(秒)\t错误信息"]
        for record in self._records:
            lines.append(
                "\t".join(
                    [
                        record.timestamp,
                        record.kind,
                        record.title,
                        record.source,
                        record.target,
                        record.status,
                        f"{record.duration:.1f}",
                        record.error,
                    ]
                )
            )
        target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        return target
