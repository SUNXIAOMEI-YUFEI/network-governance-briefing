"""OpenAI-兼容 Chat Completions 的极简客户端。

兼容以下端点：
- OpenRouter：base_url = https://openrouter.ai/api/v1
- 各种中转站（如 chr1）：base_url = https://pro.chr1.com/v1
- OpenAI 官方：base_url = https://api.openai.com/v1
- Anthropic 原生走 OpenAI 兼容层也行

不引第三方依赖，纯 urllib。避开 httpx/openai SDK 的环境锁定问题。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ============================================================
# .env 加载（不依赖 python-dotenv）
# ============================================================

def load_env(env_path: Path) -> None:
    """读 .env 文件，把 KEY=VALUE 塞进 os.environ。已存在的不覆盖。"""
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# ============================================================
# 配置
# ============================================================

@dataclass
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    concurrency: int = 4
    max_retries: int = 3
    timeout_sec: int = 60

    @classmethod
    def from_env(cls) -> "LLMConfig":
        # 先自动加载 .env
        project_root = Path(__file__).resolve().parent.parent
        load_env(project_root / ".env")

        api_key = os.environ.get("LLM_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "缺少 LLM_API_KEY。请复制 .env.example → .env 并填入中转 key。"
            )

        return cls(
            api_key=api_key,
            base_url=os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
            model=os.environ.get("LLM_MODEL", "anthropic/claude-3.5-sonnet"),
            concurrency=int(os.environ.get("LLM_CONCURRENCY", "4")),
            max_retries=int(os.environ.get("LLM_MAX_RETRIES", "3")),
            timeout_sec=int(os.environ.get("LLM_TIMEOUT_SEC", "60")),
        )


# ============================================================
# Chat Completions 调用
# ============================================================

class LLMError(Exception):
    pass


def chat_completion(
    cfg: LLMConfig,
    *,
    system: str,
    user: str,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    response_format_json: bool = True,
) -> str:
    """调 /chat/completions，返回 assistant message content（字符串）。

    - temperature=0：评分场景追求稳定性
    - response_format_json：要求输出 JSON（OpenAI / OpenRouter 大部分模型支持）
    """
    url = f"{cfg.base_url}/chat/completions"
    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format_json:
        payload["response_format"] = {"type": "json_object"}

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
            # OpenRouter 要求（不强制但友好）
            "HTTP-Referer": "https://local.briefing",
            "X-Title": "CAC-Briefing-Scorer",
        },
    )

    last_err: Exception | None = None
    for attempt in range(1, cfg.max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=cfg.timeout_sec) as resp:
                body = resp.read().decode("utf-8")
                obj = json.loads(body)
                choices = obj.get("choices") or []
                if not choices:
                    raise LLMError(f"响应无 choices：{body[:400]}")
                content = choices[0].get("message", {}).get("content", "")
                if not content:
                    raise LLMError(f"响应 content 为空：{body[:400]}")
                return content
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:800]
            except Exception:
                pass
            last_err = LLMError(f"HTTP {e.code} on attempt {attempt}: {err_body}")
            # 4xx（除 429/408）不重试
            if e.code in (400, 401, 403, 404):
                raise last_err
        except urllib.error.URLError as e:
            last_err = LLMError(f"Network error on attempt {attempt}: {e.reason}")
        except (TimeoutError, json.JSONDecodeError) as e:
            last_err = LLMError(f"Attempt {attempt} failed: {type(e).__name__}: {e}")

        # 指数退避
        if attempt < cfg.max_retries:
            time.sleep(2 ** (attempt - 1))

    raise last_err or LLMError("unknown error")


def extract_json(text: str) -> dict:
    """从 LLM 输出里提取 JSON 对象。

    优先直接 json.loads；失败时尝试抠 ```json ... ``` 或 { ... } 片段。
    """
    text = text.strip()
    # 1. 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 2. 抠 ```json ... ```
    if "```" in text:
        parts = text.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{") and p.endswith("}"):
                try:
                    return json.loads(p)
                except json.JSONDecodeError:
                    continue
    # 3. 抠第一个 { 到最后一个 }
    if "{" in text and "}" in text:
        snippet = text[text.find("{"): text.rfind("}") + 1]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            pass
    raise LLMError(f"无法从 LLM 输出提取 JSON：{text[:400]}")
