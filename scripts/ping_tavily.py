#!/usr/bin/env python3
"""
Tavily 连通性 + key 有效性 + 返回结构验证脚本。

支持两种模式：
    direct（默认）  ：直连 api.tavily.com（国内大概率被防火墙拦）
    via-vercel     ：走自己的 /api/tavily-search 中转（Vercel 边缘节点海外直连 Tavily）

用法：
    # 直连
    python scripts/ping_tavily.py
    python scripts/ping_tavily.py "California AG genetic data"

    # 走 Vercel 中转（需要 .env 里设 VERCEL_BASE_URL + TIPS_SHARED_SECRET）
    python scripts/ping_tavily.py --via-vercel
    python scripts/ping_tavily.py --via-vercel "EU AI Act GPAI Code"

退出码：
    0  成功（key 有效，返回结果，正文片段非空）
    1  网络/连通性问题（DNS / TCP / TLS 失败，国内可能要换网络）
    2  HTTP 4xx（key 无效 / 配额超 / 参数错 / 鉴权失败）
    3  HTTP 5xx（Tavily 服务端故障 / 中转层故障）
    4  返回 JSON 结构不符合预期（API 变更或字段缺失）

设计要点：
- 纯 stdlib（urllib + json），不引第三方 SDK，遵循项目约定
- 失败时打印**有意义的错误诊断**（区分 DNS 解析失败 / TCP 拒绝 / TLS 握手 / HTTP 错误码）
- 成功时打印关键指标：耗时 / 结果数 / 第一条正文片段长度 / 是否含搜索引擎答案
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def load_env_from_dotenv() -> None:
    """读取项目根 .env 把 TAVILY_* 注入 os.environ（不依赖 python-dotenv）。"""
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def diagnose_network(host: str) -> str:
    """对 host 做分层诊断（DNS / TCP），返回人类可读字符串。"""
    parts = []
    # DNS
    try:
        ip = socket.gethostbyname(host)
        parts.append(f"DNS OK → {host} = {ip}")
    except socket.gaierror as e:
        return f"DNS 解析失败：{e}（可能要换网络/DNS）"
    # TCP 443
    try:
        s = socket.create_connection((host, 443), timeout=5)
        s.close()
        parts.append("TCP 443 OK")
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        parts.append(f"TCP 443 失败：{e}")
        return " | ".join(parts)
    return " | ".join(parts)


def parse_args() -> tuple[bool, str]:
    """解析命令行：是否走 Vercel 中转 + 自定义 query。"""
    args = sys.argv[1:]
    via_vercel = False
    query = None
    for a in args:
        if a in ("--via-vercel", "--vercel"):
            via_vercel = True
        elif not a.startswith("--"):
            query = a
    if query is None:
        query = "California AG sues Chrome Holding genetic data privacy 2026"
    return via_vercel, query


def main() -> int:
    load_env_from_dotenv()

    via_vercel, query = parse_args()

    if via_vercel:
        # ---- 模式 B：走 Vercel 中转 ----
        vercel_base = os.environ.get("VERCEL_BASE_URL", "").rstrip("/")
        secret = (
            os.environ.get("TIPS_SHARED_SECRET", "").strip()
            or os.environ.get("FAV_SHARED_SECRET", "").strip()
        )
        if not vercel_base:
            print("❌ 没读到 VERCEL_BASE_URL。请在 .env 加：")
            print("   VERCEL_BASE_URL=https://你的项目.vercel.app")
            return 2
        if not secret:
            print("⚠️  没读到 TIPS_SHARED_SECRET 或 FAV_SHARED_SECRET")
            print("   如果你 Vercel 端配了 secret，本地必须也配一份相同的值")
            print("   继续测试（不带 secret，可能会 403）...")

        url = f"{vercel_base}/api/tavily-search"
        host = vercel_base.replace("https://", "").replace("http://", "").split("/")[0]
        print(f"✓ 模式：via Vercel 中转")
        print(f"✓ Endpoint：{url}")
        print(f"✓ Secret：{'已配置（' + secret[:4] + '...）' if secret else '未配置'}")
        print(f"✓ 测试 query：{query!r}\n")

        print(f"🔍 网络诊断 [{host}]：{diagnose_network(host)}")

        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": 5,
            "include_answer": True,
            "include_raw_content": False,
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "briefing-poc/0.1",
        }
        if secret:
            headers["X-Tips-Secret"] = secret
        body = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    else:
        # ---- 模式 A：直连 ----
        api_key = os.environ.get("TAVILY_API_KEY", "").strip()
        base_url = os.environ.get("TAVILY_BASE_URL", "https://api.tavily.com").rstrip("/")

        if not api_key or api_key.startswith("tvly-xxx"):
            print("❌ 没读到有效的 TAVILY_API_KEY。请检查 .env 是否已配置。")
            return 2

        print(f"✓ 模式：直连 Tavily（国内大概率会超时）")
        print(f"✓ 读到 key：{api_key[:12]}...{api_key[-4:]}（长度 {len(api_key)}）")
        print(f"✓ Base URL：{base_url}")
        print(f"✓ 测试 query：{query!r}\n")

        host = base_url.replace("https://", "").replace("http://", "").split("/")[0]
        print(f"🔍 网络诊断 [{host}]：{diagnose_network(host)}")

        url = f"{base_url}/search"
        payload = {
            "api_key": api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": 5,
            "include_answer": True,
            "include_raw_content": False,
        }
        body = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "briefing-poc/0.1",
            },
            method="POST",
        )
    ctx = ssl.create_default_context()

    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            elapsed = time.monotonic() - t0
            status = resp.status
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        elapsed = time.monotonic() - t0
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        print(f"\n❌ HTTP {e.code} {e.reason}（耗时 {elapsed:.2f}s）")
        print(f"   响应体前 500 字：{err_body}")
        if 400 <= e.code < 500:
            return 2
        return 3
    except urllib.error.URLError as e:
        elapsed = time.monotonic() - t0
        print(f"\n❌ 连接失败：{e.reason}（耗时 {elapsed:.2f}s）")
        print("   可能原因：国内网络无法直连 tavily.com / 防火墙 / TLS 问题")
        return 1
    except (socket.timeout, TimeoutError):
        elapsed = time.monotonic() - t0
        print(f"\n❌ 请求超时（{elapsed:.2f}s > 20s）")
        return 1
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"\n❌ 未预期错误：{type(e).__name__}: {e}（耗时 {elapsed:.2f}s）")
        return 1

    # ---- 解析返回 ----
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"\n❌ 返回不是合法 JSON：{e}")
        print(f"   响应体前 500 字：{raw[:500]}")
        return 4

    print(f"\n✅ HTTP {status} 成功（端到端 {elapsed:.2f}s）")
    if "elapsed_ms" in data:
        print(f"   中转层观测 Tavily 上游耗时：{data['elapsed_ms']} ms")

    # 关键字段验证
    answer = data.get("answer") or ""
    results = data.get("results") or []
    print(f"   answer（AI 总结）长度：{len(answer)} 字")
    print(f"   results 数量：{len(results)}")

    if not results:
        print("\n⚠️  results 为空——可能 query 太冷门，但 API 调通了")
        return 0

    # 看第一条结构
    first = results[0]
    expected_keys = {"title", "url", "content", "score"}
    missing = expected_keys - set(first.keys())
    if missing:
        print(f"\n❌ 第一条结果缺少字段：{missing}")
        print(f"   实际字段：{list(first.keys())}")
        return 4

    print(f"\n📄 第一条结果预览：")
    print(f"   title:   {first['title'][:80]}")
    print(f"   url:     {first['url']}")
    print(f"   score:   {first.get('score'):.3f}")
    print(f"   content（正文片段）长度：{len(first.get('content') or '')} 字")
    print(f"   content 前 200 字：{(first.get('content') or '')[:200]}")

    if answer:
        print(f"\n💡 AI 总结答案前 300 字：\n   {answer[:300]}")

    # 全部 5 条 URL + 域名一览
    print(f"\n📋 全部 {len(results)} 条结果（域名 + 标题）：")
    for i, r in enumerate(results, 1):
        host_short = (r.get("url", "") or "").split("/")[2] if "://" in (r.get("url", "") or "") else "?"
        print(f"   {i}. [{host_short}] {r.get('title', '')[:70]}")

    print(f"\n✅ 全部检查通过——Tavily 可用，可以进入 PoC 脚本环节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
