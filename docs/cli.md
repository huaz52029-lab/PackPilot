# PackPilot 命令行参考

所有命令与 GUI 共用核心层（`app/core`），因此行为、错误信息与安全策略完全一致。

## 全局参数

| 参数 | 说明 |
| --- | --- |
| `--version`, `-V` | 输出版本号（例如 `PackPilot 1.0.0`） |
| `--quiet`, `-q` | 不输出进度条（可放在任意位置） |

## compress

```bash
packpilot compress <target> <sources...> [选项]
```

| 选项 | 说明 |
| --- | --- |
| `--format` | 强制指定格式（`zip`/`7z`/`tar`/`tar.gz`/`tar.bz2`/`tar.xz`），默认按扩展名推断 |
| `--level` | `fastest`、`fast`、`normal`（默认）、`high`、`ultra` |
| `--password` | 密码，仅 7Z 支持；其它格式会返回明确错误 |
| `--volume-size` | 分卷大小，支持 `100MB`、`1G`、`1048576` 等写法 |

## extract

```bash
packpilot extract <archive> [target] [选项]
```

| 选项 | 说明 |
| --- | --- |
| `--password` | 加密压缩包的密码 |
| `--on-conflict` | `overwrite`、`skip`、`rename`（默认）、`fail` |
| `--entries` | 仅解压指定条目，逗号分隔；选择目录会包含其子项 |

未指定 `target` 时使用智能解压：若压缩包内已有唯一顶层目录则解压到压缩包所在目录，
否则解压到以压缩包名命名的子目录。

## list

```bash
packpilot list <archive> [--password P] [--json]
```

`--json` 输出结构化数据（格式、加密状态、分卷、条目列表等），便于脚本处理。

## test

```bash
packpilot test <archive> [--password P] [--json]
```

返回码 `0` 表示通过，`1` 表示发现损坏条目（失败条目会在输出中列出）。

## convert

```bash
packpilot convert <source> <target> [选项]
```

| 选项 | 说明 |
| --- | --- |
| `--level` | 目标压缩包等级 |
| `--password` | 源压缩包密码 |
| `--target-password` | 目标压缩包密码（仅 7Z） |
| `--volume-size` | 输出分卷大小 |

转换失败时不会删除源文件，也不会留下未完成的输出文件。

## hash

```bash
packpilot hash <files...> [--algorithm md5|sha1|sha256|sha512] [--json] [--output 结果.txt]
```

## verify-volumes

```bash
packpilot verify-volumes <archive.001> [--json]
```

按清单逐卷计算 SHA-256 并检查缺失分卷；返回码 `1` 表示分卷不完整。

## associations

```bash
packpilot associations install|remove|status [--no-default]
```

- `install`：注册文件关联与右键菜单（`--no-default` 时不修改默认打开方式，只加入“打开方式”列表）；
- `remove`：移除 PackPilot 注册的关联与右键菜单，并恢复原有默认程序；
- `status`：输出每个扩展名的关联状态。

## 退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 业务失败（损坏、空间不足、密码错误等） |
| 2 | 参数错误 |
| 3 | 用户取消或中断 |
