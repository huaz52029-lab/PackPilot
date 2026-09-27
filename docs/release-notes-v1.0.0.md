# PackPilot 1.0.0

PackPilot 的首个正式版本：面向 Windows 的压缩包管理器，中文界面，支持 ZIP、7Z、TAR 系列。

## 下载

- `PackPilot-1.0.0-Setup.exe`：安装版（Windows 10 / 11 64 位，无需管理员权限）
- `PackPilot-1.0.0-Portable.zip`：便携版（解压后运行 `PackPilot\PackPilot.exe`）
- `SHA256SUMS.txt`：上述两个文件的 SHA-256 校验值

## 主要功能

- 压缩 / 解压 / 浏览 / 测试 / 格式转换（ZIP、7Z、TAR、TAR.GZ、TAR.BZ2、TAR.XZ）
- 批量压缩与批量解压，统一任务中心（进度、速度、剩余时间、取消）
- 分卷压缩与完整性校验（缺失分卷会明确提示）
- 密码保护：7Z 使用 AES-256，可加密文件头
- 哈希校验（MD5 / SHA-1 / SHA-256 / SHA-512）
- Windows 文件关联与资源管理器右键菜单（仅当前用户，可完整卸载）
- 命令行：`compress`、`extract`、`list`、`test`、`convert`、`hash`、`verify-volumes`、`associations`

## 安全设计

- 解压前校验路径，拒绝 `..`、绝对路径、盘符与 UNC 路径（Zip Slip 防护）
- 默认不覆盖已有文件
- 解压前检查磁盘空间，不足时禁止开始任务
- 符号链接与设备条目默认跳过

## 已知限制

- 不支持写入加密 ZIP（Python 标准库限制），请在需要密码时使用 7Z
- 不支持解压 AES 加密的 ZIP（压缩方式 99）
- TAR 系列不提供逐条目 CRC，也不记录单条目压缩后大小

## 运行环境

Windows 10 / 11（64 位）。发布包已内置 Python 运行时，无需安装 Python 或开发环境。
