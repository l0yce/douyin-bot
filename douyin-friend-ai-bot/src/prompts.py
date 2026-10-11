# -*- coding: utf-8 -*-
"""提示词组装：把聊天记录整理成给 AI 的上下文。纯逻辑、便于测试。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_SYSTEM = (
    "你是「{friend}」在抖音上的一位朋友，正在用抖音私信和对方聊天。"
    "请用自然、口语化的中文回复，语气像真人朋友，简短（一般一两句话），"
    "不要用 Markdown/列表/标题/表情符号堆砌，不要提你是 AI 或机器人，"
    "不要问『有什么可以帮你』这类客服话，不要每条都反问。根据上下文自然地接话。"
    "标点像真人手机打字：陈述句结尾不加句号，多个短句之间用空格分隔；"
    "以吗、呢、吧、啊等语气词结尾的问句不加问号；只在情绪强烈时偶尔用感叹号。"
)

DEFAULT_USER_TEMPLATE = (
    "以下是和「{friend}」的抖音私信最近聊天记录（越靠后越新）：\n"
    "{history}\n\n"
    "现在对方刚发来这条消息：\n{last_message}\n\n"
    "请直接用一句自然的回复接这句话。"
)

# 对方发图时：文字说明里不写图片内容，图片会作为附件一起发给视觉模型
DEFAULT_IMAGE_USER_TEMPLATE = (
    "以下是和「{friend}」的抖音私信最近聊天记录（越靠后越新）：\n"
    "{history}\n\n"
    "现在对方发来以下消息（附图与消息的对应关系见说明，图片内容请看附件）：\n{last_message}\n"
    "请像真人朋友一样，用一句自然的口语回复这张图（可以说你看到了什么、"
    "接住对方的梗或情绪，不要评价图片清晰度，不要问『这是什么图』）。"
)

IMAGE_PLACEHOLDER = "[图片]"


def message_text(m: dict[str, Any]) -> str:
    """取一条消息用于展示的文字：图片消息显示为 [图片]。"""
    text = str(m.get("text", "")).strip()
    kind = str(m.get("kind") or "")
    if (not text) and kind == "image":
        return IMAGE_PLACEHOLDER
    return text


def normalize_history(messages: list[dict[str, Any]], max_items: int = 8) -> list[dict[str, Any]]:
    """messages 最新在前，转成时间正序，截取最近 max_items 条有内容的（文字或图片）。"""
    ordered = []
    for m in reversed(messages):
        if str(m.get("text", "")).strip() or str(m.get("kind") or "") == "image":
            ordered.append(m)
    return ordered[-max_items:]


def format_history(messages: list[dict[str, Any]]) -> str:
    lines = []
    for m in messages:
        who = "对方" if not m.get("from_me") else "我"
        text = message_text(m).replace("\n", " ")
        lines.append(f"{who}：{text}")
    return "\n".join(lines) if lines else "（没有更多历史记录）"


# 人设文件缓存：路径 -> (mtime, 内容)。改文件不用重启，下一条回复就生效
_PERSONA_CACHE: dict[str, tuple[float, str]] = {}


def load_persona_file(path: str | Path) -> str:
    persona_path = Path(path)
    stat = persona_path.stat()
    key = str(persona_path)
    cached = _PERSONA_CACHE.get(key)
    if cached and cached[0] == stat.st_mtime:
        return cached[1]
    text = persona_path.read_text(encoding="utf-8")
    _PERSONA_CACHE[key] = (stat.st_mtime, text)
    return text


def build_system_prompt(friend: str, cfg_prompts: dict[str, Any]) -> str:
    """system 提示词：优先人设文件（system_prompt_file），其次 config 内联字符串，最后内置默认。

    {friend} 用字面替换而不是 str.format：人设文件里出现其他花括号也不会炸。
    """
    persona_file = str(cfg_prompts.get("system_prompt_file") or "").strip()
    template = ""
    if persona_file:
        try:
            template = load_persona_file(persona_file)
        except OSError:
            template = ""  # 运行中文件被移走时回退到内联/默认，不让回复循环挂掉
    if not template:
        template = cfg_prompts.get("system_prompt") or DEFAULT_SYSTEM
    prompt = template.replace("{friend}", friend)
    notes = cfg_prompts.get("friend_notes")
    if isinstance(notes, dict):
        note = str(notes.get(friend) or "").strip()
        if note:
            prompt = prompt.rstrip() + "\n\n# 本次聊天对象备注\n" + note.replace("{friend}", friend)
    return prompt


def build_user_prompt(
    friend: str,
    messages: list[dict[str, Any]],
    last_message: str,
    cfg_prompts: dict[str, Any],
    max_history: int = 8,
    *,
    image: bool = False,
) -> str:
    if image:
        template = cfg_prompts.get("image_user_template") or DEFAULT_IMAGE_USER_TEMPLATE
    else:
        template = cfg_prompts.get("user_template") or DEFAULT_USER_TEMPLATE
    history = format_history(normalize_history(messages, max_history))
    rendered = template.format(friend=friend, history=history, last_message=last_message)
    if "{last_message}" not in template:
        rendered += "\n\n[当前完整消息]\n" + last_message
    return rendered
