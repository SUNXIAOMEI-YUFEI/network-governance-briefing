"""一次性连通性探测：确认 .env 里的 LLM key / base_url / 模型都能正常工作。

用法：
    python3 -m app.ping_llm

成功输出类似：
    [ping_llm] base_url=https://pro.chr1.com/v1, model=anthropic/claude-3.5-sonnet
    [ping_llm] OK: pong
    [ping_llm] 你的中转站工作正常，可以进 score.py --real-llm 了

失败会打印具体错误（模型名错 / key 错 / 网络不通 / quota 用完 等），请根据提示调整 .env。
"""
from __future__ import annotations

from app.llm_client import LLMConfig, chat_completion


def ping() -> None:
    cfg = LLMConfig.from_env()
    # 不打印 key
    safe_key = cfg.api_key[:8] + "..." + cfg.api_key[-4:] if len(cfg.api_key) > 16 else "***"
    print(f"[ping_llm] api_key={safe_key}")
    print(f"[ping_llm] base_url={cfg.base_url}")
    print(f"[ping_llm] model={cfg.model}")
    print(f"[ping_llm] 发送最小请求（<10 tokens）...")

    try:
        reply = chat_completion(
            cfg,
            system="You respond in exactly one short word.",
            user="Respond with the word: pong",
            max_tokens=10,
            response_format_json=False,
        )
        print(f"[ping_llm] ✅ 回复：{reply.strip()}")
        print(f"[ping_llm] 中转站工作正常，可以进 `python3 -m app.score --real-llm` 了。")
    except Exception as e:
        print(f"[ping_llm] ❌ 失败：{type(e).__name__}: {e}")
        print("[ping_llm] 常见排查：")
        print("  1. 模型名错：换成 openai/gpt-4o-mini 或 anthropic/claude-sonnet-4 重试")
        print("  2. key 过期 / 额度用完：登录中转站后台检查")
        print("  3. base_url 错：chr1 是 https://pro.chr1.com/v1，OpenRouter 是 https://openrouter.ai/api/v1")
        print("  4. 本机网络：curl 试一下 base_url 是否可达")
        raise


if __name__ == "__main__":
    ping()
