# Linux 安装与排查

安装器支持使用 systemd 的 Linux，通过 `apt-get`、`dnf` 或 `yum` 安装依赖。无需指定云厂商。当前提供两种安装路径：

| 环境 | 安装方式 |
| --- | --- |
| Ubuntu 24.04 / Debian 12 | apt 安装 Python、Xvfb；Playwright 安装 Firefox 及系统依赖 |
| 提供 Python 3.11+ 的 dnf/yum 系统 | RPM 包安装 Firefox 系统库及 Xvfb；Playwright 下载 Firefox |

dnf/yum 路径属于本项目的兼容方案，不代表所有 RPM 发行版都经过完整验证。Playwright 的浏览器与其包版本配套，官方要求见 [Playwright 安装文档](https://playwright.dev/python/docs/intro#system-requirements)。过旧的 glibc、受限软件源或不兼容的架构仍可能导致安装失败。

## 安装前

1. 准备 Node.js 20+ 及 npm，确保 `sudo` 环境也能找到命令。脚本不自动更换 Node.js。
2. 需要 Python 3.11+。脚本依次查找 `python3.13`、`python3.12`、`python3.11`、`python3`，并检查版本。RPM 环境缺少新版时尝试安装 `python3.11`、`python3.11-pip`；软件源不提供时需自行配置合适的软件源。
3. 不要替换 `/usr/bin/python3`；它可能被系统管理工具使用。项目在独立虚拟环境中运行。
4. 项目路径不要包含空格、换行或百分号。容器中没有运行 systemd 时，请自行安装依赖并分别启动组件。

```bash
cd /opt/douyin-bot
bash scripts/install.sh --plan
sudo bash scripts/install.sh
```

计划模式只打印命令，不安装软件、不生成配置、不启动服务。需要查看指定分支时使用 `--plan --package-manager dnf`（也支持 apt、yum）。实际安装时通常自动识别即可。

## 浏览器检查

安装器下载 Firefox 后会启动一个临时无头浏览器并渲染离线页面，成功后才写入 systemd 服务。检查不使用账号 Cookie，不访问抖音，也不发送消息。

```bash
cd /opt/douyin-bot
douyin-friend-ai-bot/.venv/bin/python scripts/check_browser.py
```

- 缺少共享库：按照原始错误确认对应系统库包；RPM 软件源中的包名可能因发行版而异。
- 提示 glibc 版本不足：升级操作系统，不能仅靠重新下载浏览器解决。
- 虚拟环境版本过旧：先迁移原环境，安装器不会自动删除它或覆盖私人配置。
- 下载超时：检查服务器到依赖源的网络。抖音的家庭代理配置不会自动代理 pip、npm 或浏览器下载。

离线检查只证明浏览器启动和页面渲染可用；抖音登录、代理、媒体格式、评论读取和实际发送仍需按 README 完成验证。首次安装只启动虚拟显示和控制面板，机器人与幕间需完成配置后再启动。
