"""密码强度提示控件。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

WEAK = "弱"
MEDIUM = "中"
STRONG = "强"

_COMMON_PATTERNS = (
    "password",
    "123456",
    "qwerty",
    "abc123",
    "admin",
    "letmein",
    "iloveyou",
    "111111",
    "000000",
)


@dataclass(slots=True)
class PasswordStrength:
    """密码强度评估结果。"""

    score: int
    level: str
    suggestions: list[str]

    @property
    def label(self) -> str:
        return self.level


def evaluate_password(password: str) -> PasswordStrength:
    """评估密码强度（仅用于提示，不参与任何加密实现）。"""

    if not password:
        return PasswordStrength(score=0, level=WEAK, suggestions=["请输入密码"])
    score = 0
    suggestions: list[str] = []
    length = len(password)
    if length >= 8:
        score += 1
    else:
        suggestions.append("长度至少 8 位")
    if length >= 12:
        score += 1
    elif length < 12:
        suggestions.append("长度 12 位以上更安全")
    if re.search(r"[a-z]", password):
        score += 1
    else:
        suggestions.append("加入小写字母")
    if re.search(r"[A-Z]", password):
        score += 1
    else:
        suggestions.append("加入大写字母")
    if re.search(r"\d", password):
        score += 1
    else:
        suggestions.append("加入数字")
    if re.search(r"[^\w\s]", password):
        score += 1
    else:
        suggestions.append("加入符号（如 !@#%）")
    lowered = password.lower()
    if any(pattern in lowered for pattern in _COMMON_PATTERNS):
        score = max(0, score - 3)
        suggestions.append("避免常见弱口令")
    if re.fullmatch(r"(.)\1+", password):
        score = max(0, score - 3)
        suggestions.append("避免重复同一字符")

    if score >= 5:
        return PasswordStrength(score=score, level=STRONG, suggestions=suggestions)
    if score >= 3:
        return PasswordStrength(score=score, level=MEDIUM, suggestions=suggestions)
    return PasswordStrength(score=score, level=WEAK, suggestions=suggestions)


class PasswordStrengthWidget(QWidget):
    """密码强度进度条与提示文字。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bar = QProgressBar(self)
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._label = QLabel("密码强度：-", self)
        self._label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._bar)
        layout.addWidget(self._label)
        self.set_password("")

    def set_password(self, password: str) -> PasswordStrength:
        strength = evaluate_password(password)
        value = min(100, strength.score * 20)
        if not password:
            value = 0
        self._bar.setValue(value)
        color = {"弱": "#dc2626", "中": "#d97706", "强": "#16a34a"}.get(strength.level, "#6b7280")
        self._bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}")
        hint = "、".join(strength.suggestions[:3])
        if password:
            self._label.setText(f"密码强度：{strength.level}" + (f"（建议：{hint}）" if hint else ""))
        else:
            self._label.setText("密码强度：-")
        return strength
