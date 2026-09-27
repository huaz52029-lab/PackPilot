# 开发说明

## 环境准备

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

依赖：PySide6、py7zr（运行），pytest、pytest-qt、ruff、pyinstaller（开发）。

## 常用命令

```powershell
# 启动 GUI
.\.venv\Scripts\python.exe -m app.main

# 启动 CLI
.\.venv\Scripts\python.exe -m app.cli.commands --version

# 代码检查
.\.venv\Scripts\python.exe -m ruff check app tests

# 自动修复可修复问题
.\.venv\Scripts\python.exe -m ruff check app tests --fix

# 运行全部测试
.\.venv\Scripts\python.exe -m pytest -q

# 只运行引擎测试
.\.venv\Scripts\python.exe -m pytest tests/test_zip_engine.py -q

# GUI 测试使用离屏渲染
$env:QT_QPA_PLATFORM = "offscreen"; .\.venv\Scripts\python.exe -m pytest tests/test_gui_smoke.py -q
```

## 测试环境变量

测试与本地调试可重定向配置、日志与临时目录，避免污染真实用户环境：

| 变量 | 作用 |
| --- | --- |
| `PACKPILOT_CONFIG_DIR` | 覆盖 `%APPDATA%\PackPilot` |
| `PACKPILOT_LOG_DIR` | 覆盖 `%LOCALAPPDATA%\PackPilot` |
| `PACKPILOT_TEMP_DIR` | 覆盖临时目录 |
| `QT_QPA_PLATFORM=offscreen` | GUI 无界面运行（CI 与测试） |

## 版本号规则

版本号只在 `app/version.py` 中定义：

- GUI 标题与关于对话框；
- CLI `--version`；
- PyInstaller 打包（`PackPilot.spec` 读取 `app.version`）；
- Inno Setup 安装程序（`scripts/build_installer.ps1` 生成 `installer/version.iss`）。

修改版本号时只需改动 `app/version.py`，其余位置自动同步。

## 打包

```powershell
python scripts/build.py                 # 图标 → Ruff → pytest → PyInstaller
python scripts/build.py --skip-tests    # 仅打包
python scripts/build.py --clean         # 先清理 dist/build
```

产物：`dist\PackPilot\PackPilot.exe`（GUI 模式，`console=False`）。

验证：

```powershell
.\dist\PackPilot\PackPilot.exe --version
```

GUI 子系统程序的 `--version` 通过 `AttachConsole(ATTACH_PARENT_PROCESS)` 输出到调用它的控制台。

## 安装程序

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_installer.ps1
```

脚本行为：

1. 从 `app/version.py` 读取版本号，生成 `installer/version.iss` 与 `installer/version_info.txt`；
2. 查找 Inno Setup 6 的 `ISCC.exe`（默认安装路径）；
3. 编译 `installer/PackPilot.iss`，产物输出到 `dist\PackPilot-<版本>-Setup.exe`。

未安装 Inno Setup 时会输出安装指引，配置仍会生成，可用 Inno Setup 编译器手工打开。

## 新增压缩格式的步骤

1. 在 `app/core/archive_info.py` 的 `ArchiveFormat` 中登记格式（扩展名、tar 模式、密码能力等）；
2. 新建引擎模块实现 `ArchiveEngine` 接口；
3. 在 `ArchiveManager.engine_for()` 中注册；
4. 在 `tests/` 中添加创建、解压、测试、损坏文件与安全用例；
5. 更新 README 的支持格式表与 CHANGELOG。

## 提交规范

提交信息使用 `feat:` / `fix:` / `test:` / `docs:` / `release:` 前缀，并保持一次提交对应一类改动。
