# SpeedTest

[![Build Windows](https://github.com/Forensax/SpeedTest/actions/workflows/build.yml/badge.svg)](https://github.com/Forensax/SpeedTest/actions/workflows/build.yml)
[![Release](https://img.shields.io/github/v/release/Forensax/SpeedTest)](https://github.com/Forensax/SpeedTest/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

简洁的 Windows 下载测速工具，基于 `spd.py` 的并发流式下载逻辑，支持直连与代理。

## 下载

在 [Releases](https://github.com/Forensax/SpeedTest/releases/latest) 下载 **SpeedTest.exe**，双击运行。支持 Windows 10/11 x64，无需安装 Python。

每个版本附带 `SHA256SUMS.txt`。可使用 PowerShell 核对下载文件：

```powershell
Get-FileHash .\SpeedTest.exe -Algorithm SHA256
```

## 功能

- 实时速度、平均速度、峰值、累计流量、运行时间和最近 60 秒曲线。
- 可展开查看每个测速线程的状态、当前速度、累计流量和实际下载地址；点击表头时按当时数据排序，实时刷新不会自动调整顺序，行控件会复用以避免闪烁，地址支持自动换行。
- 默认 8 路并发、手动停止，可调整连接数、下载地址和定时停止。
- 内置 Apple CDN、Hugging Face Models、GitHub Releases 三种测速集合，可直接切换或恢复集合默认地址。
- 支持 HTTP、HTTPS、SOCKS5 代理及账号认证，SOCKS5 通过代理解析 DNS。
- 测速与设置分为两个页面，运行期间锁定设置。

默认使用原脚本中的 8 个 Apple 固件下载地址。Hugging Face 集合包含 Qwen3-8B 和 Qwen3-Embedding-8B 的大型模型分片，GitHub Releases 集合包含 8 个固定版本、单文件超过 1 GiB 的 ComfyUI、Godot 和 LLVM 发布资产。数据读取后直接丢弃，不保存下载文件。测速会持续产生网络流量，结果反映所选下载源与连接路径的吞吐量。

速度按十进制显示：`1 MB/s = 8 Mbps`；累计流量中的 MB、GB 同样按十进制换算。

## 设置

在“设置”页配置后保存，再到“测速”页开始测速。

| 设置 | 默认值 / 行为 |
| --- | --- |
| 并发连接 | 8，范围 1–64；按地址列表循环分配 |
| 定时停止 | 关闭；启用后可设 1–86400 秒 |
| 代理类型 | 直连；所有连接均按界面设置执行 |
| 手动代理 | 地址 `127.0.0.1`，端口 `10808` |
| 测速集合 | Apple CDN、Hugging Face Models、GitHub Releases、Custom |
| 下载地址 | 每行一个 HTTP 或 HTTPS 直链 |
| 配置文件 | `%LOCALAPPDATA%\SpeedTest\config.json` |
| 代理密码 | 仅当前会话使用，关闭后需要重新填写 |

直连模式不读取系统或环境变量中的代理。连接超时为 5 秒，读取超时为 2 秒，失败后等待 5 秒重试；停止会中断重试等待，并在连接回收后恢复开始按钮。配置文件损坏时使用默认设置并提示。

## 源码运行

需要 64 位 Python 3.14。以下命令使用独立虚拟环境，路径可按本机情况调整：

```powershell
py -3.14 -m venv D:\PATH\venv\SpeedTest
& D:\PATH\venv\SpeedTest\Scripts\python.exe -m pip install -r requirements.txt
& D:\PATH\venv\SpeedTest\Scripts\python.exe main.py
```

需要本地代理安装依赖时，在 pip 命令中加入 `--proxy http://127.0.0.1:10808`。

## 测试与打包

```powershell
& D:\PATH\venv\SpeedTest\Scripts\python.exe -m pip install -r requirements-build.txt
& D:\PATH\venv\SpeedTest\Scripts\python.exe -m unittest discover -s tests -v
& D:\PATH\venv\SpeedTest\Scripts\python.exe tools/build.py
```

测试使用本地 HTTP 和 SOCKS5 服务，覆盖并发、停止、重试、定时结束、代理认证、配置与 GUI 状态。打包脚本使用 PyInstaller 6.22.3，验证 EXE 启动后生成 `dist/SpeedTest.exe` 和 `dist/SHA256SUMS.txt`。

GitHub Actions 在推送 `main`、提交 PR 或手动触发时测试并打包；推送 `v*` 标签后自动发布 Release。发布前同步应用版本与 Windows 文件版本；标签须与应用版本一致，例如 `v0.1.0`。

## 代码结构

- `spd.py`：保留的原始命令行脚本。
- `main.py`：GUI 入口。
- `speedtest_gui/config.py`：配置验证、代理参数和配置存储。
- `speedtest_gui/engine.py`：测速引擎及线程安全快照。
- `speedtest_gui/app.py`：中文界面及实时曲线。
- `tests/`：本地自动化测试。
- `tools/build.py`：打包、启动检查与校验值生成。

## 许可

[MIT](LICENSE) · Copyright © 2026 Forensax
