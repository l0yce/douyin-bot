# -*- coding: utf-8 -*-
"""抖音网页版私信页的页面选择器（集中管理）。

抖音前端经常改版，这里用“一组备选选择器”按顺序尝试，提高兼容性。
如果某天全部失效，程序会保存页面快照/截图，你只需对照截图更新这里即可。
参考了多个开源项目 2026 年仍在使用的选择器写法。
"""
from __future__ import annotations

# 聊天主页面（新版；若打开后被重定向到 /messages 也无需担心）
CHAT_URL = "https://www.douyin.com/chat"
MESSAGES_URL = "https://www.douyin.com/messages"

# 已登录特征：出现私信导航 / 搜索框
LOGIN_MARKERS = (
    'text=私信',
    'input[placeholder*="搜索"]',
    '[role="textbox"][placeholder*="搜索"]',
    '[data-e2e="conversation-item"]',
)

# 未登录特征
LOGIN_REQUIRED_MARKERS = (
    'text=扫码登录',
    'text=验证码登录',
    'text=登录后',
)

# 风控/安全验证特征
RISK_MARKERS = (
    'text=安全验证',
    'text=完成验证',
    'text=验证身份',
    'text=请完成安全验证',
)

# 左侧会话列表：一个会话 = 一个 data-e2e="conversation-item"（该元素就是整行容器）
# 重要：行内大量子元素（头像/预览/时间戳等）的 class 也含 "ConversationItem" 字样，
# 绝不能对它们做模糊匹配，否则会把一个会话拆成十几个假条目（重复名字、日期、预览）。
CONVERSATION_ITEM = '[data-e2e="conversation-item"]'
CONVERSATION_ITEM_FALLBACKS = (
    '.conversationConversationItemwrapper',
)

# 会话标题（昵称/备注名）选择器：只匹配标题本身，先精确 class 再模糊兜底
CONVERSATION_TITLES = (
    '.conversationConversationItemtitle',
    '[class*="conversationConversationItemtitle"]',
)

# 搜索框（新版聊天页顶部）
SEARCH_INPUTS = (
    'input.semi-input[placeholder="搜索"]',
    'input[placeholder*="搜索"]',
    '[role="textbox"][placeholder*="搜索"]',
    'input[aria-label*="搜索"]',
)

# 搜索结果面板里的条目
SEARCH_RESULT_ITEMS = (
    '[class*="SearchPanelitem"]',
    '.SearchPanelitembox',
)

# 聊天区域已经打开的标记（data-e2e="msg-input" 为新版界面实测）
CHAT_OPEN_MARKERS = (
    '[data-e2e="msg-input"]',
    '[class*="RightPanelHeader"]',
    '[class*="chatHeader"]',
    '[class*="ChatHeader"]',
    '[class*="messageEditor"]',
    '[data-e2e="msg-item-content"]',
)

# 消息输入框（contenteditable / Draft.js / editor-kit 新版编辑器）
MESSAGE_INPUTS = (
    '[data-e2e="msg-input"] [contenteditable="true"]',
    '[class*="messageEditorinputArea"]',
    '[data-contents="true"]',
    '.DraftEditor-editor [contenteditable="true"]',
    '.DraftEditor-root [contenteditable="true"]',
    '[contenteditable="true"][data-placeholder*="发送消息"]',
    '[contenteditable="true"][aria-label*="消息"]',
    '.messageEditorimChatEditorContainer [data-slate-editor="true"][contenteditable="true"]',
    '[contenteditable="true"]',
    'textarea[placeholder*="消息"]',
)

# 发送按钮（点不到按钮时按 Enter）
SEND_BUTTONS = (
    '[class*="messageMsgInputpublishBtn"]',
    '.e2e-send-msg-bt',
    'button[aria-label*="发送"]',
    '[role="button"][aria-label*="发送"]',
)

# 消息列表容器与单条消息
MESSAGE_LIST = (
    '.messageMessageListlist',
    '[class*="messageMessageListlist"]',
    '[class*="message-list"]',
    '[class*="MessageList"]',
)
MESSAGE_BOXES = (
    '[class*="messageMessageBoxmessageBox"]',
    '[class*="messageBox"]',
)
MESSAGE_CONTENT = '[data-e2e="msg-item-content"]'
FROM_ME_MARKER = '[class*="isFromMe"]'
MESSAGE_TIME = '[class*="time"]'

SEND_FAILURE_MARKERS = (
    'text=发送失败',
    '[class*="sendFailed"]',
    '[class*="SendFailed"]',
)

# 会话行上的未读红点/数字徽章（semi-design badge，抖音红 rgb(254,44,85)）。
# 只在“有新消息未读”的会话行上出现；class 名随版本变，用关键词模糊匹配。
CONVERSATION_UNREAD_BADGE = (
    '[class*="ConversationItemUnReadCount"]',
    '[class*="im-saas-unreadCountBadge"]',
)
