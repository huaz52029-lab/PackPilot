# PackPilot 架构说明

## 分层结构

```text
GUI (PySide6)      CLI (argparse)
      \              /
       \            /
        任务层 tasks/        （QThreadPool + QRunnable，仅 GUI 使用）
              |
        核心层 app/core/      （纯 Python，不依赖 PySide6）
              |
    zipfile / tarfile / py7zr / hashlib / pathlib
```

- **核心层（`app/core`）**：所有压缩、解压、测试、转换、分卷、哈希与安全校验逻辑。
  GUI 与 CLI 都通过 `ArchiveManager` 调用，不存在第二套压缩实现。
- **任务层（`app/tasks`）**：把核心层操作包装为可取消的后台任务，负责进度、速度、
  预计剩余时间、状态机与任务历史。压缩/解压/测试/转换/哈希/扫描均在子线程执行，GUI 主线程不会被阻塞。
- **服务层（`app/services`）**：配置（JSON）、最近使用、任务历史、日志、临时文件管理。
- **界面层（`app/gui`）**：主窗口、压缩包浏览视图、任务中心面板、对话框与自定义控件。
- **Windows 集成（`app/windows`）**：注册表文件关联、资源管理器右键菜单、ShellExecuteEx 调用。
- **命令行（`app/cli`）**：与 GUI 共用核心层的命令实现。

## 核心数据结构

| 类型 | 作用 |
| --- | --- |
| `ArchiveFormat` | 支持的压缩格式枚举，附带扩展名、tar 模式、密码能力等元数据 |
| `ArchiveEntry` | 压缩包内单个条目：名称、目录标记、原始/压缩大小、修改时间、CRC、加密与链接标记 |
| `ArchiveInfo` | 压缩包整体信息：格式、条目列表、注释、加密状态、分卷列表、警告、实际可读路径 |
| `CreateOptions` / `ExtractOptions` / `ConvertOptions` | 各操作的参数对象 |
| `SafetyLimits` | 安全阈值：压缩率、总大小、条目数量、路径长度、符号链接策略 |
| `TestReport` / `ExtractResult` | 测试与解压的结果统计 |

## 引擎接口

所有格式实现同一接口（`app/core/engine_base.py`）：

```python
class ArchiveEngine(ABC):
    def list_entries(source, *, password) -> ArchiveInfo
    def create(options, *, progress, is_cancelled) -> Path
    def extract(source, options, *, progress, is_cancelled, ask) -> ExtractResult
    def test(source, *, password, progress, is_cancelled) -> TestReport
    def add_files(path, sources, ...) -> list[str]     # 7Z / TAR 通过重写实现
    def delete_entries(path, names, ...) -> int
```

`source` 既可以是磁盘路径，也可以是分卷读取器（`VolumeReader`），因此分卷压缩包的浏览、解压与测试
无需先生成完整的临时文件（零拷贝读取）。

## 分卷设计

PackPilot 采用与 7-Zip 一致的字节切分方式：

```text
project.zip.001 + project.zip.002 + project.zip.003 = project.zip
```

同时写入 `project.zip.packpilot-parts.json` 清单，记录每卷的名称、大小与 SHA-256：

- 读取时自动识别 `.001` 形式的分卷集合，并按清单或序号连续性校验；
- 缺失分卷会明确提示（例如 `project.zip.002`），并在任务开始前阻止操作；
- 没有清单时仍可读取（兼容 7-Zip 生成的分卷），但完整性检查只能依赖序号与大小；
- `VolumeReader` 把多个分卷暴露为一个可随机读取的文件对象，`zipfile` / `tarfile` / `py7zr` 可直接使用。

## 任务模型

```text
TaskManager（QObject，主线程）
   └── QThreadPool（默认 2 个线程）
         └── BaseTask（QRunnable）
               ├── TaskContext：进度 / 取消 / 日志
               └── TaskSignals：started / progress / message / finished
```

- 任务状态：等待 → 运行中 → 完成 / 失败 / 取消；
- 进度包含百分比、当前文件、已完成/总数、速度、已用时间与预计剩余时间；
- 取消通过 `threading.Event` 实现，核心层在复制/读取循环中定期检查并在取消时清理未完成的输出；
- 任务结束时写入历史（`%APPDATA%\PackPilot\history.json`）。

## 线程安全

- Qt 信号跨线程自动排队，任务状态更新统一回到主线程处理；
- `TaskInfo` 的读写通过锁保护，对外提供快照副本；
- 7Z 解压使用 py7zr 的 `WriterFactory` 流式写入，写入工厂内部使用锁保护目标路径决策。

## 错误处理

核心层定义了明确的业务异常（`app/core/errors.py`）：
`UnsupportedFormatError`、`OperationNotSupportedError`、`ArchiveCorruptedError`、
`UnsafeArchiveError`、`PasswordRequiredError`、`WrongPasswordError`、
`InsufficientSpaceError`、`VolumeMissingError`、`TaskCancelledError`、`EntryNotFoundError`。

底层库的限制（例如标准库无法写入加密 ZIP）会转换为 `OperationNotSupportedError`，
界面与 CLI 如实展示，不会伪造成功。
