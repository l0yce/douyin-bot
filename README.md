# douyin-bot · 抖音 AI 好友助手

[![公开版检查](https://github.com/l0yce/douyin-bot/actions/workflows/check.yml/badge.svg)](https://github.com/l0yce/douyin-bot/actions/workflows/check.yml)

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

安装脚本适用于使用 **systemd** 的 Linux，自动识别 **apt、dnf 或 yum**，分别安装 Python 和 Firefox 所需依赖。推荐 Ubuntu 24.04 / Debian 12；dnf/yum 系统会使用 RPM 依赖安装方案，并在启动服务前检查 Firefox 能否运行。发行版要求和排查见 [Linux 安装说明](docs/LINUX.md)。需要：

1. Python **3.11 或以上**、Node.js **20 或以上**及 npm。
2. 你自己有权使用、能正常登录网页版私信的抖音账号。
3. 兼容 OpenAI Chat Completions 的模型接口地址、API Key 和模型名。理解图片需要支持图片的模型。
4. 能使用 SSH 和编辑 JSON 文件。系统软件源需提供所需依赖，Python 不足 3.11 时需先安装受支持的新版本。

**如果部署在云服务器上，先准备家庭／住宅代理出口。** 本项目原部署遇到过机房 IP 无法正常登录抖音的情况，采用家庭出口访问抖音。代理应让抖音看到家庭宽带的出口 IP；换成另一台机房服务器的普通代理不能达到同样目的。不是所有机房 IP 都必然失败，家庭出口也不能保证免除登录验证。

```text
云服务器上的机器人 → 家庭／住宅代理 → 家庭宽带出口 IP → 抖音
```

需要自行准备可从云服务器连接的家庭出口：可以是自己家庭网络中的转发服务，也可以是自己有权使用的住宅代理服务。仓库不附带代理地址、账号或现成节点。详见 [家庭代理出口的准备与配置](docs/PROXY.md)。

面板需要控制服务，此版安装方案以 root 运行，适合由你独立管理的服务器。不要把面板交给不可信用户。

## 1. 下载并安装

先安装 Git，按服务器的包管理器选择一条：

```bash
sudo apt-get update
sudo apt-get install -y git
# dnf 系统改用：sudo dnf install -y git
# yum 系统改用：sudo yum install -y git
```

然后下载项目：

```bash
sudo git clone https://github.com/l0yce/douyin-bot.git /opt/douyin-bot
cd /opt/douyin-bot
# 先安装符合要求的 Node.js 和 npm，再执行下面的脚本。
sudo bash scripts/install.sh
```

Node.js 安装说明见 [官网](https://nodejs.org/en/download)。

可先运行 `bash scripts/install.sh --plan` 查看安装计划，此操作不会修改系统。不要覆盖系统自带 Python；脚本会优先选择已有的 Python 3.11 或以上版本。

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

### 配置家庭／住宅代理出口

这里的目的，是让抖音访问请求从家庭／住宅 IP 发出，而不是从云服务器的机房 IP 发出。HTTP、SOCKS5 只是连接代理的协议，不能说明出口是否为家庭 IP。应先确认代理的最终出口，再导入 Cookie 和检查登录。直接连接已能正常使用的环境可以留空。

在 `config.json` 的 `account` 中设置：

```json
"proxy_server": "socks5://127.0.0.1:1080"
```

HTTP 代理可填写 `http://127.0.0.1:7890`；不使用代理填 `""`。**这里的 127.0.0.1 指运行机器人的服务器，不是你自己的电脑。** 只在电脑上开代理，不会自动让服务器经过它。

**端口区分：`1080` / `7890` 是代理示例端口，`8790` 是控制面板端口。** 代理端口按你实际的代理服务填写；如果代理也在机器人服务器上运行，请避开面板已占用的 `8790`。

该设置用于抖音浏览器和视频评论读取，不会自动用于幕间或基础 AI 模型接口；邮件提醒通过 SMTP 直连。代理服务须已经启动，且服务器能够连接它。修改后重启机器人：

```bash
sudo systemctl restart douyin-friend-ai-bot
```

需要认证的代理、如何检查连接和如何搭建 SSH 转发，见 [代理出口配置与排查](docs/PROXY.md)。示例地址不是可直接使用的公共代理，仓库不包含任何真实代理凭据。

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

### 推荐：在控制面板里添加

先完成上面的代理、模型配置和抖音登录，并确保账号已经与目标好友有私信会话。不需要手动抄写 `sec_uid`，面板可以运行发现脚本读取身份信息。

1. 按下方 [打开面板](#打开面板) 的说明访问网页，输入令牌，点击 **保存并进入**。
2. 打开 **运行状态**。如果机器人正在运行，点击 **停止机器人** 并确认，等待服务状态变为 **已停止**。初次安装尚未启动机器人时可跳过停止操作。
3. 切换到 **好友白名单**（手机底部显示 **好友**），点击 **发现好友**。
4. 在弹出的“发现脚本（只读，不发消息）”窗口点击 **运行**，等待提示 **发现脚本完成**。运行期间不要启动机器人。
5. 在输出中找到目标好友，核对显示名和抖音号，复制该好友的整行 **可复制白名单项：{...}**。复制你自己的实际结果，不要复制文档里的示例身份。
6. 点击 **关闭**，回到白名单页面，点击 **粘贴添加好友**。粘贴刚复制的那一行，点击 **解析并添加**。
7. 核对列表中新出现的好友及右侧开关。开启表示允许回复，关闭表示不允许回复；需要时可展开 **账号核验信息** 检查身份。最后务必点击页面右上方 **保存修改**，等到出现 **已保存** 提示。**“解析并添加”只添加到当前页面，尚未写入服务器。**
8. 添加多位好友时，逐位重复复制、粘贴和解析，最后统一保存。首次部署继续完成下面的演练和幕间启动；已经正常运行过的部署，保存后回到 **运行状态 → 启动机器人** 恢复运行，再让该好友发一条新消息验证。

发现脚本只读取最近会话，不发送消息。找不到好友时，先在抖音账号中建立或打开与该好友的私信会话，再重新发现。不要把自己的账号加入白名单。

> 调整已有好友的启用开关后，同样需要点击 **保存修改**。若要暂停所有自动回复，直接在“运行状态”点击“停止机器人”。

### 备用：通过命令行添加

不使用面板时，可在机器人未运行的情况下执行：

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

三个字段都要填写，不能用示例值启动。通过面板复制实际发现结果时，无需再手工编辑这些字段。发现前先停止机器人，避免浏览器 profile 被占用。

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

这里的 `ssh -L` 只用于打开控制面板，与上面的家庭代理出口是两回事。命令中的第一个 `8790` 是电脑的本地端口，第二个是服务器的面板端口。如果电脑的 `8790` 已被占用，可改为 `ssh -L 8791:127.0.0.1:8790 root@你的服务器地址`，随后访问 `http://127.0.0.1:8791/`；服务器面板端口无需更改。

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
- [代理出口配置与排查](docs/PROXY.md)
- [隐私与公开发布说明](docs/PRIVACY.md)
- [测试与验证范围](docs/VALIDATION.md)
- [依赖检查记录](docs/DEPENDENCIES.md)
- [幕间上游文档](douyin-interlude/upstream/README.md)（含其他平台说明，本仓库接入的是抖音）

## 来源与许可证

幕间引擎来自 [MomoiCore / HDS-Interlude](https://gitee.com/MomoiCore/HDS-Interlude)。保留上游源码、说明及许可证，并加入抖音桥接、消息上下文和面板适配，详见 [第三方说明](THIRD_PARTY_NOTICES.md)。

本项目按 [AGPL-3.0](LICENSE) 发布。使用有权使用的账号，让参与聊天的好友了解自动回复功能，请勿用于群发骚扰。
