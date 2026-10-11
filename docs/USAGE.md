# 配置、日常使用与常见问题

## 配置在哪里

以下均相对于安装根目录，默认 `/opt/douyin-bot`：

| 文件 | 用途 |
| --- | --- |
| `douyin-friend-ai-bot/config/config.json` | 抖音账号、模型、白名单、轮询、回复限制 |
| `douyin-friend-ai-bot/config/persona.md` | 本地角色人设 |
| `douyin-interlude/bridge-config.json` | 桥接密钥、剧情和幕间参数 |
| `douyin-bot-panel/panel_token` | 面板访问令牌 |
| `douyin-bot-panel/data/fault-alerts.json` | 邮箱及 SMTP 授权码 |

示例文件的 `.example` 后缀表示可以公开分享。本地实际配置不要上传。

## 常用设置

### 在面板管理白名单

首次添加的完整步骤见 [README：添加白名单好友](../README.md#4-添加白名单好友)。操作路径为：

**运行状态 → 停止机器人 → 好友白名单 → 发现好友 → 运行 → 复制目标好友的“可复制白名单项” → 关闭 → 粘贴添加好友 → 解析并添加 → 保存修改。**

这些按钮完成好友发现和配置写入，不需要在终端手工填写身份绑定值。发现期间不能同时启动机器人。

添加后可在好友卡片右侧切换启用状态，点击 **保存修改** 才写入服务器；展开 **账号核验信息** 可以核对或复制 `sec_uid`。保存成功后，已有正常部署可在“运行状态”启动机器人；首次部署还需按 README 完成演练和幕间服务启动。

“发现好友”不会自动把发现的所有人加入白名单，需逐位选取输出并粘贴添加。当前建议每次粘贴一位好友的完整条目，避免整段输出混入其他文字或多个对象导致解析失败。若提示缺少字段，应重新复制完整条目，不要猜测 `sec_uid`。

需要暂停所有自动回复时，在“运行状态”停止机器人；不要仅靠将白名单全部关闭来代替停止服务。

### 聊天与回复参数

- `reply.conversation_context.history_messages`：给幕间参考的近期双方消息条数，示例为 7，范围 5–100。仅包括当前网页读取到的消息，不会凭空补齐旧消息。
- `reply.conversation_context.cancel_on_manual`：生成或等待发送期间你手动接话时，取消相关待发回复。
- `reply.video_comments`：开关、条数、每条最大长度、缓存小时数、总超时秒数。读取不到时使用可见卡片信息，不会虚构评论。
- `reply.max_per_friend_per_hour` / `max_per_friend_per_day`：每位好友的发送上限。
- `ai.vision_model`：图片模型；留空时不要期待机器人真正理解图片。
- `interlude.enabled`：默认开启。关闭后改为基础即时 AI 回复流程，不能使用幕间剧情与主动联系。

修改模型地址、Key 或模型名后，重启幕间和机器人。修改 `persona.md` 后，已有剧情可能仍保留旧设定；请在幕间面板中同步更新角色设定。面板会提示某些设置是否需要重启。

## 主动联系

示例 `allowProactiveMessages` 为 `false`，默认不开启。需要时在幕间完整参数中调整运行参数和主动联系预算，并确保只针对你的白名单联系人。

`agency.proactiveDailyCap` 限制幕间主动联系的预算；实际是否发出还取决于剧情、休息窗口、上下文和发送限制。打开开关不会保证每天一定发消息，也不应作为精准定时消息工具。

## 运行维护

服务名称：`douyin-friend-ai-bot`、`douyin-interlude`、`douyin-bot-panel`、`douyin-bot-xvfb`；可选故障监视器为 `douyin-fault-alerts.timer`。

停止聊天但保留面板：

```bash
sudo systemctl stop douyin-friend-ai-bot
```

重新导入 Cookie、扫码或发现好友前，先停止机器人，操作完成后再启动，防止 profile 被两个进程占用。发生验证码时人工处理，程序不会自动绕过。

日志：机器人 `logs/runtime.log` 和 `logs/reply_audit.jsonl`，幕间使用 `journalctl -u douyin-interlude`。审计日志包含聊天内容，分享问题时先脱敏。

## 更新代码

请先查看更新说明，备份你自己的本地配置、`state/`、幕间和面板的 `data/`。这些数据不在 Git 版本管理中，`git pull` 不能替你备份它们。

```bash
cd /opt/douyin-bot
sudo systemctl stop douyin-friend-ai-bot douyin-interlude
sudo git pull --ff-only
sudo bash scripts/install.sh
sudo systemctl start douyin-interlude douyin-friend-ai-bot
```

安装器保留现有配置，不自动迁移未来版本的新字段。若有迁移要求，请按版本说明操作。

## 修改并构建幕间引擎

引擎快照保留在 `douyin-interlude/upstream/src/`，运行入口是 `upstream/lib/index.js`。修改 TypeScript 后，在 `douyin-interlude/` 执行：

```bash
PUPPETEER_SKIP_DOWNLOAD=true npm ci
npm run build
npm run check
```

`npm run build` 用 esbuild 从 TypeScript 重新生成 CommonJS 运行包，不依赖上游 monorepo 的工作区。若修改对外类型接口，还需同步类型声明。上游文档中的 QQ、Puppeteer 等功能不属于本仓库默认部署路径；默认关闭幕间浏览器功能，不需要下载 Chromium。

## 常见问题

**服务 active，但不回消息？**

按顺序检查：抖音是否登录、白名单三个身份字段、好友是否发了新消息、AI 配置、幕间是否 ready、发送限额、日志是否有失败或待核实的发送。幕间也可能根据剧情选择不立即回复。

**提示“视频无法查看”？**

这是聊天网页可能给出的占位提示。程序只在本条消息里查视频编号，找不到就无法读取评论；不能靠附近的其他视频链接猜测。

**有视频编号但没有评论？**

可能没有可读取的评论数据，或网页加载、登录、网络、超时导致失败。查看 `video_comments` 日志，区分 `ok`、空评论和不可用。评论参考不等于观看视频，排序也不是“最新五条”的保证。

**填了人设但模型不知道游戏和梗？**

本项目不自带实时网络知识库。请在本地人设或设定中提供必要背景，不能认为模型已经联网查询。

**SSH 隧道打不开面板？**

检查隧道是否保持连接，服务器上 `douyin-bot-panel` 是否运行，电脑 8790 是否已被占用。面板只监听服务器本机地址是正常行为。

**QQ 邮箱测试失败？**

检查邮箱 SMTP 开关、16 位授权码、服务器是否能访问 `smtp.qq.com:465`。测试成功表示 SMTP 接受邮件，仍需检查收件箱或垃圾邮件。

**内存和费用？**

浏览器和幕间各占资源，实际消耗取决于页面和配置。幕间剧情推进、记忆整理和主动联系也可能调用模型；视频评论不使用视频视觉输入，但评论文字仍会计入生成请求上下文。按自己的预算调整推进周期和上下文条数。
