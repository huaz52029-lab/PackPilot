"""一键构建：代码检查 → 测试 → PyInstaller 打包。

用法：

    python scripts/build.py                # 完整构建（含测试）
    python scripts/build.py --skip-tests   # 仅打包
    python scripts/build.py --skip-lint
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"


def run(command: list[str], *, description: str) -> None:
    print(f"\n=== {description} ===")
    print("$ " + " ".join(command))
    result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if result.returncode != 0:
        raise SystemExit(f"步骤失败（退出码 {result.returncode}）：{description}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PackPilot 构建脚本")
    parser.add_argument("--skip-tests", action="store_true", help="跳过 pytest")
    parser.add_argument("--skip-lint", action="store_true", help="跳过 ruff 检查")
    parser.add_argument("--keep-build", action="store_true", help="保留 build 目录")
    parser.add_argument("--clean", action="store_true", help="构建前清理 dist/build")
    args = parser.parse_args(argv)

    python = sys.executable
    started = time.monotonic()

    if args.clean:
        for directory in (DIST_DIR, BUILD_DIR):
            if directory.exists():
                print(f"清理 {directory}")
                shutil.rmtree(directory)

    run([python, "scripts/make_icon.py"], description="生成应用图标")

    if not args.skip_lint:
        run([python, "-m", "ruff", "check", "app", "tests"], description="Ruff 代码检查")

    if not args.skip_tests:
        run([python, "-m", "pytest", "-q"], description="运行测试")

    run(
        [
            python,
            "-m",
            "PyInstaller",
            "PackPilot.spec",
            "--noconfirm",
            "--clean",  # 避免使用旧的缓存 TOC（否则可能保留过期的依赖收集结果）
            "--distpath",
            str(DIST_DIR),
        ],
        description="PyInstaller 打包",
    )

    if not args.keep_build:
        work_dir = BUILD_DIR / "PackPilot"
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)

    executable = DIST_DIR / "PackPilot" / "PackPilot.exe"
    elapsed = time.monotonic() - started
    print(f"\n构建完成，用时 {elapsed:.1f} 秒")
    if executable.exists():
        print(f"可执行文件：{executable}")
        print("下一步（可选）：powershell -ExecutionPolicy Bypass -File scripts/build_installer.ps1")
    else:
        print("⚠ 未找到生成的 EXE，请检查 PyInstaller 输出")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
