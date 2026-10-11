# -*- coding: utf-8 -*-
"""AI 客户端：调用 OpenAI 兼容的 /chat/completions 接口。

默认对接 DeepSeek；其它任何 OpenAI 兼容服务（如硅基流动、Kimi、通义等）
只需改 config.json 里的 ai.base_url / ai.model / ai.api_key。

看图回复：文本模型看不了图时，可用 ai.vision_model（如 GLM-4.5V）专门处理
对方发来的图片消息——把图片以 base64 一起发给视觉模型。
"""
from __future__ import annotations

import base64
import json
import re
import time
from typing import Any

import httpx


class AIError(RuntimeError):
    pass


class AIClient:
    def __init__(self, cfg: dict[str, Any]) -> None:
        ai = cfg["ai"]
        self.base_url = str(ai["base_url"]).rstrip("/")
        self.api_key = str(ai["api_key"]).strip()
        self.model = str(ai["model"]).strip()
        self.vision_model = str(ai.get("vision_model") or "").strip() or self.model
        self.temperature = float(ai.get("temperature", 1.0))
        self.max_tokens = int(ai.get("max_tokens", 300))
        # 思考型视觉模型（GLM-4.5V）的 reasoning_tokens 和正文共用 max_tokens 预算：
        # 预算太小思考就吃光额度、正文为空，视觉调用单独给更大的预算
        self.vision_max_tokens = int(ai.get("vision_max_tokens", 1000) or 1000)
        self.timeout = float(ai.get("timeout_seconds", 90))
        self.max_retries = int(ai.get("max_retries", 2))
        if not self.api_key:
            raise AIError("ai.api_key 为空")
        if not self.base_url:
            raise AIError("ai.base_url 为空")

    def _endpoint(self) -> str:
        # 兼容两种写法：https://api.deepseek.com/v1 或 https://api.deepseek.com
        base = self.base_url
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1") or base.endswith("/v1/"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def chat(self, messages: list[dict[str, str]], image_b64: str | None = None,
             model: str | None = None, max_tokens: int | None = None) -> str:
        """messages 为普通文本消息。若 image_b64 非空，则把最后一条 user 消息
        改成“文字 + 图片”的多模态内容，并改用视觉模型（vision_model）。"""
        use_model = model or (self.vision_model if image_b64 else self.model)
        payload_messages: list[dict[str, Any]] = messages
        if image_b64:
            payload_messages = self._attach_image(messages, image_b64)
        token_budget = max_tokens or (self.vision_max_tokens if image_b64 else self.max_tokens)
        payload = {
            "model": use_model,
            "messages": payload_messages,
            "temperature": self.temperature,
            "max_tokens": token_budget,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(self._endpoint(), headers=headers, json=payload)
                if resp.status_code == 200:
                    # 响应体坏掉（网关截断、返回 HTML 等）当作临时故障参与重试
                    try:
                        data = resp.json()
                        choice = data["choices"][0]
                        content = self._clean(str(choice["message"].get("content") or ""))
                    except Exception as exc:  # noqa: BLE001
                        last_error = AIError(f"AI 返回了无法解析的内容: {exc}")
                    else:
                        if content:
                            return content
                        # 空正文绝不静默：多半是思考型模型的 reasoning_tokens 吃光了
                        # max_tokens 预算（finish_reason=length），带细节参与重试
                        usage = (data.get("usage") or {}).get("completion_tokens_details") or {}
                        last_error = AIError(
                            "AI 返回了空正文（finish_reason=%s, reasoning_tokens=%s）"
                            % (data.get("choices", [{}])[0].get("finish_reason"),
                               usage.get("reasoning_tokens"))
                        )
                elif resp.status_code in (401, 403):
                    raise AIError(f"AI 接口鉴权失败（HTTP {resp.status_code}）：请检查 api_key")
                elif resp.status_code in (429, 500, 502, 503, 504):
                    last_error = AIError(f"AI 接口暂时不可用（HTTP {resp.status_code}）")
                else:
                    snippet = resp.text[:300]
                    raise AIError(f"AI 接口返回异常 HTTP {resp.status_code}: {snippet}")
            except httpx.TimeoutException as exc:
                last_error = AIError(f"AI 接口超时（{self.timeout}s）: {exc}")
            except httpx.HTTPError as exc:
                last_error = AIError(f"AI 接口网络错误: {exc}")
            except AIError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise AIError(f"AI 调用失败: {exc}") from exc

            if attempt < self.max_retries:
                time.sleep(2 + attempt * 2)
        raise AIError(f"AI 接口多次重试仍失败：{last_error}")

    @staticmethod
    def _attach_image(messages: list[dict[str, str]], image_b64: str) -> list[dict[str, Any]]:
        """把 base64 图片附到最后一条 user 消息上（OpenAI 多模态格式）。"""
        out: list[dict[str, Any]] = []
        for idx, msg in enumerate(messages):
            if idx == len(messages) - 1 and msg.get("role") == "user":
                text = str(msg.get("content", "")).strip()
                parts: list[dict[str, Any]] = []
                if text:
                    parts.append({"type": "text", "text": text})
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                })
                out.append({"role": "user", "content": parts})
            else:
                out.append(msg)
        return out

    @staticmethod
    def _clean(text: str) -> str:
        text = text.strip()
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = re.sub(r"^[\"'\u201c\u2018]+|[\"'\u201d\u2019]+$", "", text).strip()
        return text
