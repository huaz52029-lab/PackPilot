"""把 ``resources/icons/packpilot.svg`` 渲染为多尺寸 ``packpilot.ico``。

仅依赖 PySide6（QSvgRenderer / QImage），生成的 ICO 使用 PNG 压缩条目，
兼容 Windows 11 与 PyInstaller/Inno Setup。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SVG = PROJECT_ROOT / "resources" / "icons" / "packpilot.svg"
DEFAULT_ICO = PROJECT_ROOT / "resources" / "icons" / "packpilot.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render_png(svg_path: Path, size: int) -> bytes:
    """把 SVG 渲染为指定尺寸的 PNG 字节。"""

    renderer = QSvgRenderer(QByteArray(svg_path.read_bytes()))
    if not renderer.isValid():
        raise RuntimeError(f"无法解析 SVG：{svg_path}")
    image = QImage(QSize(size, size), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("PNG 编码失败")
    return bytes(buffer.data())


def build_ico(svg_path: Path, target: Path) -> Path:
    """把多个尺寸的 PNG 打包成 ICO 文件。"""

    images = [render_png(svg_path, size) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = bytearray()
    payload = bytearray()
    for size, data in zip(SIZES, images, strict=True):
        dimension = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset)
        payload += data
        offset += len(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(bytes(header + entries + payload))
    return target


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    svg_path = Path(arguments[0]) if arguments else DEFAULT_SVG
    target = Path(arguments[1]) if len(arguments) > 1 else DEFAULT_ICO
    icon = build_ico(svg_path, target)
    print(f"已生成图标：{icon}（{icon.stat().st_size} 字节，{len(SIZES)} 种尺寸）")
    return 0


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
