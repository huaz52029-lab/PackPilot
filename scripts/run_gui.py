"""开发环境启动 GUI 的便捷脚本（等价于 python -m app.main）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import main

if __name__ == "__main__":
    raise SystemExit(main([]))
