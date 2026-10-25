"""OpenAI 兼容 LLM 客户端（httpx 直连，不引第三方 SDK）。

支持任意 OpenAI 兼容接口：OpenAI / DeepSeek / 通义 / Moonshot / Ollama /
vLLM / 公司内部网关（如学途工作台提供的 OpenAI 兼容端点）。
配置项：OPSPILOT_LLM_BASE_URL / OPSPILOT_LLM_API_KEY / OPSPILOT_LLM_MODEL。
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> dict:
    """从模型回复中提取 JSON：优先整体解析，再尝试代码块/首尾花括号。"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start: end + 1])
        except json.JSONDecodeError:
            pass
    raise LLMError("模型回复中未找到合法 JSON")


def chat(messages: list[dict], temperature: float = 0.1) -> str:
    """调用 /chat/completions，返回文本内容。失败抛 LLMError。"""
    settings = get_settings()
    if not settings.llm_configured:
        raise LLMError("LLM 未配置（OPSPILOT_LLM_BASE_URL / API_KEY / MODEL 为空）")
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": temperature,
    }
    try:
        resp = httpx.post(
            url, json=payload, headers=headers, timeout=settings.llm_timeout_s
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise LLMError(f"LLM 请求失败: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM 返回非 JSON: {exc}") from exc

    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"LLM 返回结构异常: {data}") from exc


def chat_json(messages: list[dict]) -> dict:
    """要求模型输出 JSON 并解析。优先使用 json_object 响应模式，不支持则降级。"""
    settings = get_settings()
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=settings.llm_timeout_s)
        if resp.status_code == 400:
            # 部分兼容网关不支持 response_format，去掉后重试
            payload.pop("response_format")
            resp = httpx.post(url, json=payload, headers=headers, timeout=settings.llm_timeout_s)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return _extract_json(content or "")
    except httpx.HTTPError as exc:
        raise LLMError(f"LLM 请求失败: {exc}") from exc
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMError(f"LLM 返回无法解析: {exc}") from exc
