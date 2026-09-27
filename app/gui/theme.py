"""界面主题：浅色 / 深色 / 跟随系统，以及 QSS 样式表。"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle, QStyleFactory

from app.services.settings import THEME_DARK, THEME_LIGHT, THEME_SYSTEM

logger = logging.getLogger(__name__)

ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
DANGER = "#dc2626"
SUCCESS = "#15803d"
WARNING = "#b45309"


def system_prefers_dark() -> bool:
    """判断系统是否使用深色模式（Windows 读取注册表，其它平台看调色板）。"""

    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return int(value) == 0
        except OSError:
            pass
    app = QApplication.instance()
    if app is not None:
        base = app.palette().color(QPalette.ColorRole.Window)
        return base.lightness() < 128
    return False


def resolve_theme(theme: str) -> str:
    """把 ``system`` 解析为实际的 ``light`` / ``dark``。"""

    if theme == THEME_SYSTEM:
        return THEME_DARK if system_prefers_dark() else THEME_LIGHT
    return THEME_DARK if theme == THEME_DARK else THEME_LIGHT


def stylesheet_path(theme: str) -> Path:
    resource = Path(__file__).resolve().parents[2] / "resources" / "styles" / f"{theme}.qss"
    if resource.exists():
        return resource
    return Path(getattr(sys, "_MEIPASS", "")) / "resources" / "styles" / f"{theme}.qss"


def load_stylesheet(theme: str) -> str:
    """读取 QSS；资源缺失时返回内置样式，保证界面可用。"""

    resolved = resolve_theme(theme)
    path = stylesheet_path(resolved)
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover
            logger.warning("读取样式表失败：%s（%s）", path, exc)
    return FALLBACK_DARK if resolved == THEME_DARK else FALLBACK_LIGHT


def apply_theme(app: QApplication, theme: str) -> str:
    """应用主题并返回实际使用的主题名。"""

    if app.style() is None or not app.style().objectName():
        app.setStyle(QStyleFactory.create("Fusion"))
    resolved = resolve_theme(theme)
    app.setStyleSheet(load_stylesheet(resolved))
    app.setProperty("packpilot_theme", resolved)
    palette = app.palette()
    palette.setColor(
        QPalette.ColorRole.Highlight,
        QColor(ACCENT),
    )
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)
    return resolved


class DenseStyle(QProxyStyle):
    """更紧凑的表格行高（Windows 11 默认行高偏大）。"""

    def pixelMetric(self, metric, option=None, widget=None) -> int:  # type: ignore[override]
        if metric == QStyle.PixelMetric.PM_TableHeaderHeight:
            return 26
        return super().pixelMetric(metric, option, widget)


FALLBACK_LIGHT = f"""
QMainWindow, QDialog {{ background: #f5f6f8; }}
QToolBar {{ background: #ffffff; border-bottom: 1px solid #e2e5ea; spacing: 4px; padding: 4px; }}
QToolButton {{ padding: 5px 8px; border-radius: 4px; }}
QToolButton:hover {{ background: #e8eefb; }}
QTreeView, QTableView, QListWidget {{ background: #ffffff; border: 1px solid #e2e5ea; }}
QTreeView::item:selected, QTableView::item:selected {{ background: {ACCENT}; color: #ffffff; }}
QHeaderView::section {{ background: #f0f2f5; padding: 4px 6px; border: none; border-right: 1px solid #e2e5ea; }}
QPushButton {{ padding: 5px 14px; border: 1px solid #c9cdd4; border-radius: 4px; background: #ffffff; }}
QPushButton:hover {{ background: #eef2ff; }}
QPushButton:default {{ background: {ACCENT}; color: #ffffff; border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #9aa0a6; }}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{ padding: 4px 6px; border: 1px solid #c9cdd4; border-radius: 4px; background: #ffffff; }}
QStatusBar {{ background: #ffffff; border-top: 1px solid #e2e5ea; }}
QProgressBar {{ border: 1px solid #d5d8de; border-radius: 4px; background: #ffffff; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QDockWidget::title {{ background: #f0f2f5; padding: 4px 8px; }}
"""

FALLBACK_DARK = f"""
QMainWindow, QDialog {{ background: #1f2125; color: #e8eaed; }}
QWidget {{ color: #e8eaed; }}
QToolBar {{ background: #26282d; border-bottom: 1px solid #35383f; spacing: 4px; padding: 4px; }}
QToolButton {{ padding: 5px 8px; border-radius: 4px; }}
QToolButton:hover {{ background: #34383f; }}
QTreeView, QTableView, QListWidget {{ background: #23252a; alternate-background-color: #26282d; border: 1px solid #35383f; }}
QTreeView::item:selected, QTableView::item:selected {{ background: {ACCENT}; color: #ffffff; }}
QHeaderView::section {{ background: #2b2e34; padding: 4px 6px; border: none; border-right: 1px solid #35383f; }}
QPushButton {{ padding: 5px 14px; border: 1px solid #43474f; border-radius: 4px; background: #2b2e34; }}
QPushButton:hover {{ background: #34383f; }}
QPushButton:default {{ background: {ACCENT}; border-color: {ACCENT}; color: #ffffff; }}
QPushButton:disabled {{ color: #7b8087; }}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{ padding: 4px 6px; border: 1px solid #43474f; border-radius: 4px; background: #23252a; }}
QStatusBar {{ background: #26282d; border-top: 1px solid #35383f; }}
QProgressBar {{ border: 1px solid #43474f; border-radius: 4px; background: #23252a; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QDockWidget::title {{ background: #2b2e34; padding: 4px 8px; }}
QMenuBar {{ background: #26282d; }}
QMenuBar::item:selected {{ background: #34383f; }}
QMenu {{ background: #26282d; border: 1px solid #35383f; }}
QMenu::item:selected {{ background: {ACCENT}; }}
"""
