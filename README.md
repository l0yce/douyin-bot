# douyin-bot · 抖音 AI 好友助手

把 **抖音私信机器人、幕间系统和网页控制面板** 放在一起的项目。

让机器人只回复白名单好友，设置角色的说话方式，参考你手动发过的消息，并在网页上管理聊天、剧情、记忆和运行状态。

仓库只有代码和公开示例，**不包含开发者的账号、Cookie、密钥、私人角色设定、好友名单或聊天记录**。下载后需要配置你自己的账号和模型。

## 能做什么

- **白名单聊天**：按昵称、抖音号和会话身份绑定好友，减少串人或误发。
- **手动聊天协作**：把当前好友会话中可读取的双方消息加入上下文；你手动接话时，可取消机器人等待发送的回复。
- **图片与表情包**：配置图片模型后理解图片；未提供可读取内容的消息会附上说明。
- **视频评论参考**：不播放视频、不提取画面或音频，尝试读取默认排序的前 5 条顶层评论，每条最多 30 字，供模型参考。面板可调整数量、长度、缓存和超时。
- **不可用视频卡片处理**：识别“视频无法查看”等占位卡片，有编号就尝试读评论，没有编号就说明内容不可见，避免把界面提示当成好友原话。
- **幕间系统**：角色设定、持续剧情、记忆、日程和可选主动联系。公开示例默认关闭主动联系。
- **手机管理面板**：查看聊天动态、白名单、日志和状态，在“剧情与记忆”中切换幕间管理、聊天协作与视频评论。
- **QQ 邮箱故障提醒**：持续故障后提醒，带重复提醒间隔和恢复通知。默认关闭，不需要额外调用 AI。

> 抖音网页可能改变页面、要求验证，或不提供某条消息的实际内容。本项目不保证所有卡片和评论都能读取。历史条数指当前网页实际读到的消息，不能保证补全未加载的旧消息。

## 三部分怎么配合

```text
抖音私信 ↔ 机器人（读取、身份校验、发送）
                   ↕ 本机认证接口
              幕间系统（生成回复、剧情与记忆）
                   ↕
              网页控制面板（设置与查看）
```

| 目录 | 作用 |
| --- | --- |
| `douyin-friend-ai-bot/` | Python + Playwright/Firefox，负责抖音收发 |
| `douyin-interlude/` | Node.js 桥接与幕间；`upstream/` 保留引擎源码及许可证 |
| `douyin-bot-panel/` | FastAPI 后端、网页前端、邮件检测 |
| `scripts/` | 初始化、安装和公开发布检查 |
| `deploy/` | systemd 模板和 Nginx 示例 |
| `docs/` | 使用、隐私与验证说明 |

## 部署前准备

安装脚本面向 **Ubuntu 24.04 / Debian 12 或更新版本**，使用 systemd 管理服务。需要：

1. Python **3.11 或以上**、Node.js **20 或以上**及 npm。
2. 你自己有权使用、能正常登录网页版私信的抖音账号。
3. 兼容 OpenAI Chat Completions 的模型接口地址、API Key 和模型名。理解图片需要支持图片的模型。
4. 能使用 SSH 和编辑 JSON 文件。其他 Linux 系统需要自行安装浏览器依赖。

面板需要控制服务，此版安装方案以 root 运行，适合由你独立管理的服务器。不要把面板交给不可信用户。

## 1. 下载并安装

在服务器执行：

```bash
sudo apt-get update
sudo apt-get install -y git
sudo git clone https://github.com/l0yce/douyin-bot.git /opt/douyin-bot
cd /opt/douyin-bot
# 先安装符合要求的 Node.js 和 npm，再执行下面的脚本。
sudo bash scripts/install.sh
```

Node.js 安装说明见 [官网](https://nodejs.org/en/download)。

脚本会安装 Python 依赖、Firefox、幕间依赖，生成本地私密配置，并启动虚拟显示和面板。**不会自动启动机器人发消息**。依赖下载失败时，按终端报错处理后重跑。现有配置不会覆盖。

## 2. 填写模型、账号和角色

编辑 `/opt/douyin-bot/douyin-friend-ai-bot/config/config.json`。下面只展示要修改的部分，**不要用它覆盖整个配置**：

```json
{
  "owner": {"douyin_id": "你自己的抖音号", "nickname": "你的昵称"},
  "ai": {
    "base_url": "https://你的模型服务地址/v1",
    "api_key": "你自己的 API Key",
    "model": "服务商提供的模型名",
    "vision_model": ""
  }
}
```

`vision_model` 留空表示没有配置图片模型。编辑同目录的 `persona.md` 填写角色设定，该文件不被 Git 跟踪。幕间首次使用这份人设；也可在面板的“剧情与记忆 → 设定”中管理剧情设定。

需要代理时，在 `account` 中增加 `proxy_server`，例如 `http://127.0.0.1:7890`。这是抖音浏览器的代理，不会自动用于 AI 模型接口。请使用自己的代理配置。

## 3. 登录抖音

服务器没有桌面时，可在自己的电脑浏览器登录抖音网页版，用你信任的 Cookie 导出方式导出 **JSON 数组**，通过 SCP/SFTP 上传为 `/opt/douyin-bot/douyin-friend-ai-bot/state/cookies.json`。Cookie 相当于登录凭据，不能发给别人。

```bash
cd /opt/douyin-bot/douyin-friend-ai-bot
chmod 600 state/cookies.json
export DISPLAY=:99
export PLAYWRIGHT_BROWSERS_PATH="$PWD/ms-playwright"
.venv/bin/python src/main.py --check-login
```

在具备图形桌面、已安装依赖的机器上，也可使用 `--login-qr` 交互扫码。不要同时让两个进程使用同一个浏览器 profile。登录失效或出现验证时需人工重新登录。

## 4. 添加白名单好友

账号应先有和目标好友的私信会话。机器人未运行时执行：

```bash
cd /opt/douyin-bot/douyin-friend-ai-bot
export DISPLAY=:99
export PLAYWRIGHT_BROWSERS_PATH="$PWD/ms-playwright"
.venv/bin/python src/main.py --discover-friends --no-ai
```

终端会输出可复制的白名单项，把选中的好友加入 `config.json` 的 `whitelist.friends` 数组：

```json
{
  "name": "聊天列表显示的昵称或备注",
  "douyin_id": "好友真实抖音号",
  "sec_uid": "发现命令输出的会话绑定值",
  "enabled": true
}
```

三个字段都要填写，不能用示例值启动；不要把自己加入白名单。以后可在面板管理白名单；执行发现前先停止机器人，避免 profile 被占用。

## 5. 演练并启动

先只检查，不调用 AI，也不发送消息：

```bash
cd /opt/douyin-bot/douyin-friend-ai-bot
export DISPLAY=:99
export PLAYWRIGHT_BROWSERS_PATH="$PWD/ms-playwright"
.venv/bin/python src/main.py --once --dry-run --no-ai
```

确认登录、好友身份和配置没有报错后启动：

```bash
sudo systemctl enable --now douyin-interlude.service
sudo systemctl enable --now douyin-friend-ai-bot.service
sudo systemctl status douyin-interlude douyin-friend-ai-bot --no-pager
```

让白名单好友发一条新消息，在面板和日志中确认实际收发。幕间可能根据上下文选择暂不回应，服务运行中不代表每条消息都立即回复。

## 打开面板

最简单的是 SSH 隧道，无需开放面板端口。在自己的电脑执行并替换服务器地址：

```bash
ssh -L 8790:127.0.0.1:8790 root@你的服务器地址
```

保持连接，浏览器打开 `http://127.0.0.1:8790/`。在服务器读取登录令牌：

```bash
sudo cat /opt/douyin-bot/douyin-bot-panel/panel_token
```

令牌只交给可信管理员。手机或域名访问可使用 `deploy/nginx.conf.example` 配置自己的 **HTTPS** 反向代理到 `/botpanel/`，不必开放 8790 或 18765 的公网端口。

面板主要入口：

- **聊天动态**：最近收到和发送的消息。
- **运行状态**：启停机器人、检查服务和配置故障邮件。
- **好友白名单**：添加、启用和停用好友。
- **剧情与记忆**：切换概况、设定、剧情、记忆、日程、完整参数、数据日志、聊天协作和视频评论。
- **运行日志**：排查读取、模型、代理、发送和评论获取问题。

## 可选：QQ 邮箱提醒

在运行状态中填写自己的发件 QQ 邮箱、收件邮箱和 **SMTP 授权码**。授权码不是 QQ 登录密码，需要在自己的邮箱设置中开启 SMTP 后获取。

保存并成功发送测试邮件后，开启提醒，在服务器执行：

```bash
sudo systemctl enable --now douyin-fault-alerts.timer
```

默认持续故障 3 分钟才提醒，间隔 120 分钟重复提醒，并支持恢复通知。邮件通过 SMTP 直连，避免抖音代理故障阻断提醒。检测器运行在同一台服务器上，**整台服务器断电或断网时无法发送邮件**。

## 常用命令与更多说明

```bash
sudo systemctl restart douyin-interlude douyin-friend-ai-bot
sudo systemctl stop douyin-friend-ai-bot
sudo journalctl -u douyin-friend-ai-bot -n 80 --no-pager
sudo journalctl -u douyin-interlude -n 80 --no-pager
```

- [配置、更新和常见问题](docs/USAGE.md)
- [隐私与公开发布说明](docs/PRIVACY.md)
- [测试与验证范围](docs/VALIDATION.md)
- [依赖检查记录](docs/DEPENDENCIES.md)
- [幕间上游文档](douyin-interlude/upstream/README.md)（含其他平台说明，本仓库接入的是抖音）

## 来源与许可证

幕间引擎来自 [MomoiCore / HDS-Interlude](https://gitee.com/MomoiCore/HDS-Interlude)。保留上游源码、说明及许可证，并加入抖音桥接、消息上下文和面板适配，详见 [第三方说明](THIRD_PARTY_NOTICES.md)。

本项目按 [AGPL-3.0](LICENSE) 发布。使用有权使用的账号，让参与聊天的好友了解自动回复功能，请勿用于群发骚扰。
