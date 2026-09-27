"""核心层异常定义。"""

from __future__ import annotations

from collections.abc import Iterable


class PackPilotError(Exception):
    """PackPilot 所有业务异常的基类。"""

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:
        return self.message


class UnsupportedFormatError(PackPilotError):
    """压缩格式无法识别或当前库不支持该操作。"""


class OperationNotSupportedError(PackPilotError):
    """底层库明确不支持所请求的能力（例如 ZIP 写入密码）。"""


class ArchiveCorruptedError(PackPilotError):
    """压缩包结构损坏或无法解析。"""


class UnsafeArchiveError(PackPilotError):
    """压缩包内存在越界路径等安全风险。"""

    def __init__(self, message: str, *, entries: Iterable[str] = (), detail: str = "") -> None:
        super().__init__(message, detail=detail)
        self.entries = list(entries)


class PasswordRequiredError(PackPilotError):
    """压缩包已加密且需要密码。"""


class WrongPasswordError(PackPilotError):
    """密码错误。"""


class InsufficientSpaceError(PackPilotError):
    """目标磁盘空间不足。"""

    def __init__(self, message: str, *, required: int = 0, available: int = 0) -> None:
        super().__init__(message)
        self.required = required
        self.available = available


class VolumeError(PackPilotError):
    """分卷集合存在问题。"""

    def __init__(self, message: str, *, missing_parts: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.missing_parts = list(missing_parts)


class VolumeMissingError(VolumeError):
    """分卷缺失。"""


class TaskCancelledError(PackPilotError):
    """用户取消了任务。"""


class EntryNotFoundError(PackPilotError):
    """压缩包中不存在指定条目。"""
