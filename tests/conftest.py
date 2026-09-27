"""pytest 公共夹具：隔离配置目录、构造测试数据。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

# 必须在创建 QApplication 之前设置，保证 GUI 测试使用离屏渲染
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def isolated_app_dirs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """把配置/日志/临时目录重定向到临时位置，避免污染真实用户环境。"""

    base = tmp_path_factory.mktemp("packpilot-env")
    os.environ["PACKPILOT_CONFIG_DIR"] = str(base / "config")
    os.environ["PACKPILOT_LOG_DIR"] = str(base / "logs")
    os.environ["PACKPILOT_TEMP_DIR"] = str(base / "temp")
    yield base


@pytest.fixture(autouse=True)
def qt_offscreen(qapp: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """GUI 测试使用离屏渲染，避免弹出真实窗口。"""

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def sample_tree(tmp_path: Path) -> Path:
    """构造包含中文路径、空文件/空目录与二进制文件的示例目录树。"""

    root = tmp_path / "测试项目"
    chinese_dir = root / "中文 文件夹"
    chinese_dir.mkdir(parents=True)
    (chinese_dir / "测试文件.txt").write_text("PackPilot 中文路径测试\n", encoding="utf-8")
    (chinese_dir / "空文件.txt").write_bytes(b"")
    nested = root / "子目录" / "更深的目录"
    nested.mkdir(parents=True)
    (nested / "data.bin").write_bytes(bytes(range(256)) * 64)
    (root / "空目录").mkdir()
    return root


@pytest.fixture
def simple_file(tmp_path: Path) -> Path:
    path = tmp_path / "简单文件.txt"
    path.write_text("PackPilot\n", encoding="utf-8")
    return path


@pytest.fixture
def large_file(tmp_path: Path) -> Path:
    """约 8 MB 的不可压缩数据（用于大文件读写测试）。"""

    path = tmp_path / "大文件.bin"
    block = os.urandom(1024 * 1024)
    with path.open("wb") as handle:
        for _ in range(8):
            handle.write(block)
    return path


def make_zip_with_entries(path: Path, entries: dict[str, bytes]) -> Path:
    """辅助函数：写入指定条目名的 ZIP（用于构造恶意压缩包）。"""

    import zipfile

    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path
