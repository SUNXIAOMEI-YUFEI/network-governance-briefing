"""工作台 v1 · 小 tips 生成核心模块（v1，2026-06-02）。

产品定位
========
用户在工作台勾选 N 条已收藏的文章 + 可选写一段独立补充，
本模块负责：
  1. 对每条文章调 Tavily（走 Vercel 中转）拿原文/公开报道
  2. 注入用户范文笔法（brain/style_samples/）
  3. 调 LLM 生成 300-500 字的「纯洞察式」小 tips（不复述事实，只产洞察）
  4. 返回结果 + 搜索日志 + 成本估算

调用方
======
- scripts/generate_tips.py（CLI 入口，本地调试用）
- scripts/tips_poc.py（PoC 时的 4 版本对比脚本）
- 未来：api/tips.js Vercel function 拿到前端请求后调本模块（服务端不需要——
  但目前 api/tips.js 只做 GitHub 同步，LLM 生成走前端→ Python CLI 模式还是
  前端直接调 LLM，需要看后续部署形态决定）

关键约束
========
- 沿用项目"纯 stdlib"约定（urllib + json + sqlite3）
- 不引第三方依赖
- 笔法注入路径硬编码 brain/style_samples/，因为该目录是用户机器上的固定位置
- LLM 调用走 app.llm_client（已支持 model_override + cost_meter 旁路）

模型选择（用户敲定 2026-06-02）
==============================
- 默认 deepseek-reasoner（max_tokens=3000，避免思考链失控）
- 快速模式 deepseek-chat（成本 1/5，速度 3 倍，质量略逊但够用）
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app.llm_client import LLMConfig, chat_completion


# ============================================================
# 路径常量
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "briefing.db"

# brain/style_samples 路径——用户机器上的固定位置
STYLE_DIR = Path(
    "/Users/pheobezhong/Library/Application Support/CodeBuddy CN/User/"
    "globalStorage/tencent-cloud.coding-copilot/brain/c4e04c6855da4793941fcc6bc2c22342/style_samples"
)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class TipArticle:
    """工作台勾选的一条文章（轻量版，比 score.Article 字段少）。"""
    id: int
    title: str
    url: str
    summary: str
    source_name: str
    source_tier: str
    content_type: str
    published_at: str
    total_score: int = 0


@dataclass
class TipResult:
    """tips 生成结果。"""
    tip_markdown: str          # 生成的洞察文本
    model: str                 # 实际用的模型
    elapsed_s: float           # 总耗时
    search_log: list[dict]     # 每条文章的搜索结果（透明可审）
    user_note_used: bool       # 是否用了用户补充
    n_articles: int            # 喂入的文章数
    n_searches: int            # 实际调了几次 Tavily
    prompt_chars: int          # 总 prompt 字数（system+user）
    output_chars: int          # 输出字数
    error: str | None = None   # 失败信息（成功时为 None）


# ============================================================
# 数据加载
# ============================================================

def load_articles_from_db(article_ids: list[int]) -> list[TipArticle]:
    """从 SQLite 读 N 条文章。
    
    保留 article_ids 的输入顺序（用户在前端勾选的顺序）。
    """
    if not article_ids:
        return []

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(article_ids))
        cur = conn.execute(
            f"""SELECT id, title, url, summary, source_name, source_tier,
                       content_type, published_at, total_score
                FROM articles WHERE id IN ({placeholders})""",
            article_ids,
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    by_id = {
        r["id"]: TipArticle(
            id=r["id"],
            title=r["title"] or "",
            url=r["url"] or "",
            summary=r["summary"] or "",
            source_name=r["source_name"] or "",
            source_tier=r["source_tier"] or "C",
            content_type=r["content_type"] or "opinion_analysis",
            published_at=r["published_at"] or "",
            total_score=int(r["total_score"] or 0),
        )
        for r in rows
    }
    # 保持传入顺序
    return [by_id[i] for i in article_ids if i in by_id]


def load_articles_from_dicts(items: list[dict]) -> list[TipArticle]:
    """从前端传过来的 favorites 数组直接构造 TipArticle，不走数据库。
    
    这是给 api/tips.js 类调用方用的——前端传过来的 favorites 已经包含完整字段。
    """
    out: list[TipArticle] = []
    for it in items:
        if not isinstance(it, dict) or it.get("id") is None:
            continue
        out.append(TipArticle(
            id=int(it["id"]),
            title=it.get("title") or "",
            url=it.get("url") or "",
            summary=it.get("summary") or "",
            source_name=it.get("source_name") or "",
            source_tier=it.get("source_tier") or "C",
            content_type=it.get("content_type") or "opinion_analysis",
            published_at=it.get("published_at") or "",
            total_score=int(it.get("total_score") or 0),
        ))
    return out


# ============================================================
# Tavily 搜索（走 Vercel 中转）
# ============================================================

ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u2060-\u206f\u00ad\u034f\u180e]")


def clean_summary(text: str) -> str:
    """清洗 KtN 邮件的零宽填充字符 + 多余空白。"""
    if not text:
        return ""
    cleaned = ZERO_WIDTH_RE.sub("", text)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def extract_query_for_tavily(article: TipArticle) -> str:
    """从 article 抽 Tavily 查询串：title + summary 前 150 字（清洗后）。"""
    title = article.title or ""
    summary_clean = clean_summary(article.summary or "")
    head = (summary_clean or "")[:150]
    query = f"{title} {head}" if head else title
    return query[:380]  # Tavily 建议 query < 400 chars


def tavily_search(
    query: str,
    *,
    vercel_base_url: str | None = None,
    secret: str | None = None,
    timeout_s: int = 30,
) -> dict:
    """调 /api/tavily-search 中转 endpoint。返回 Tavily 原始响应（dict）。
    
    走 Vercel 中转的原因：国内直连 api.tavily.com 被防火墙拦（实测 TCP 443 timeout）。
    Vercel 海外节点能直连。
    
    错误处理：
    - HTTP 错误抛 RuntimeError，调用方自己决定 fallback
    - 连接错误（timeout/refused）抛 RuntimeError
    """
    base = (vercel_base_url or os.environ.get("VERCEL_BASE_URL", "")).rstrip("/")
    if not base:
        raise RuntimeError("缺少 VERCEL_BASE_URL（请在 .env 配置）")

    secret_val = (
        secret
        or os.environ.get("TIPS_SHARED_SECRET", "").strip()
        or os.environ.get("FAV_SHARED_SECRET", "").strip()
    )

    url = f"{base}/api/tavily-search"
    payload = {
        "query": query,
        "search_depth": "basic",
        "max_results": 5,
        "include_answer": True,
        "include_raw_content": False,
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "tips-generator/1.0",
    }
    if secret_val:
        headers["X-Tips-Secret"] = secret_val

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"Tavily HTTP {e.code}: {err_body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Tavily 连接失败：{e.reason}") from e
    except (TimeoutError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Tavily 异常：{type(e).__name__}: {e}") from e


# ============================================================
# 范文笔法注入
# ============================================================

# 笔法材料缓存（避免每次生成都读文件）
_STYLE_CACHE: str | None = None


def load_style_samples(refresh: bool = False) -> str:
    """读 brain/style_samples/00_文风提炼.md 全文 + 范文片段。
    
    缓存结果——文件不会经常变。
    """
    global _STYLE_CACHE
    if _STYLE_CACHE is not None and not refresh:
        return _STYLE_CACHE

    parts: list[str] = []

    path = STYLE_DIR / "00_文风提炼.md"
    if path.exists():
        parts.append("# 用户文风提炼（务必遵守）\n\n" + path.read_text(encoding="utf-8"))

    for fn, label in (
        ("01_中美欧模型治理.md", "范文 1·中美欧模型治理"),
        ("03_开源大模型避风港.md", "范文 2·开源大模型避风港"),
    ):
        p = STYLE_DIR / fn
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        parts.append(f"\n\n# {label}（节选）\n\n" + text[:2200])

    _STYLE_CACHE = "\n\n".join(parts)
    return _STYLE_CACHE


# ============================================================
# 核心：纯洞察式 prompt
# ============================================================

INSIGHT_INSTRUCTION = """\
请直接产出一段 300-500 字的「深度洞察」，作为下一期《网络治理动态速递》的核心评论段。

## 用户的真实需求（必读）

1. **用户已经知道每条事实在讲什么**——他自己勾选的素材，对每条的来龙去脉都心里有数
2. **不要复述事实**——每条事实最多用半句话作为论据带出（例："西班牙近期推出转化立法"），
   不要出现"2026 年 X 月 X 日，西班牙政府批准了……"这种完整事实陈述
3. **要的是炒一盘菜，不是食材摆盘**——
   不要按"事实 1 → 事实 2 → 事实 3"逐条分析，
   也不要"事实层 / 观点层 / 问题层"三栏罗列，
   要的是把这些事实揉碎，提取出一条主线判断，再围绕这条主线展开论证

## 洞察的两种角度（请混合使用，不要单一）

- **网信办视角**：这些动态对中国监管者意味着什么——哪些是中国可借鉴或警示的，
  哪些暴露了海外治理的盲区，哪些在中国已经先行/落后
- **行业全景视角**：把这一批事实放在更大的时空坐标里——
  全球 AI 治理在这个时点呈现什么宏观趋势，欧美各自走到哪一步，
  老问题（数据主体权益、跨境流动、敏感数据保护）如何被新技术/新场景重新激活

## 文风约束（严格遵守用户范文笔法）

1. **第一句**：抛出本批事实背后的共同主线判断，但不要写成
   "近期围绕 X 的若干动态，呈现出一条共同的脉络"这种 AI 套话开头。
   范文示例可参考："从算法到模型，人工智能正跨越一个分水岭"
   或"开源之所以宝贵，是因为其低门槛、高透明度带来了源源不断的创意和改进"。
   要给出有信息量的判断句，不是空泛过渡句

2. **段落结构**：1-2 段，每段 200-400 字，论证型长段，不是金句型短段
   - 先抛"判断"
   - 再用"机制 + 例证"展开（事实当例证，一句话点过）
   - 用"一是 / 二是 / 三是"或"其一 / 其次 / 最后"嵌入长句串联，**不分行不加粗**

3. **数字、引语**：自然嵌入论述，不单独成行
   （例："超过85万加州居民的基因数据被泄露"嵌进句中作为论据）

4. **加粗**：≤ 2 处，且只用于关键术语**首次出现**（如"破产程序中的敏感数据保护"）

5. **结尾**：以一段平和观察或一句自然引语收住，**不下硬结论**，
   不写"真正的考验从 X 才开始"或"喘息不是和解"这种金句留白

## 严格禁用（命中即不合格）

- 复述事实（"2026 年 5 月 26 日..."）
- "近期围绕 X 的若干动态，呈现出一条共同的脉络"这种 AI 套话开头
- "更值得关注的是" / "归根结底" / "读懂了这层" / "换言之" / "不可忽视的是"
- 金句留白结尾
- "## 一、背景 / ## 二、内容" 三段式骨架
- emoji ✅❌ 符号
- 画面感开篇（"X 月 X 日傍晚"）
- "对 X 而言" 连用三段做"影响分析"

## 产出

直接输出洞察段落，不要前言后语，不要标题，不要"以下是我的洞察"这种过渡句。
"""


def build_prompt(
    articles: list[TipArticle],
    enrichments: dict[int, dict],
    user_note: str | None,
    style_text: str,
) -> tuple[str, str]:
    """返回 (system, user) 两段 prompt。"""

    system = (
        "你是腾讯研究院「大模型研究小分队」的资深写手。\n"
        "你正在为《网络治理动态速递》写一段核心评论——给中央网信办相关研究人员看。\n"
        "只关注域外（海外）网络治理动态。\n\n"
        + style_text
    )

    article_blocks = []
    for i, a in enumerate(articles, 1):
        block = [f"### 素材 {i}"]
        block.append(f"- 标题：{a.title}")
        block.append(f"- 信源：{a.source_name}（{a.source_tier} 级）")
        block.append(f"- 类型：{a.content_type}")
        block.append(f"- 时间：{a.published_at[:10]}")

        cleaned_sum = clean_summary(a.summary or "")
        if cleaned_sum:
            block.append(f"- 摘要供参考：{cleaned_sum[:500]}")

        enrich = enrichments.get(a.id)
        if enrich and not enrich.get("error"):
            answer = enrich.get("answer") or ""
            results = enrich.get("results") or []
            if answer or results:
                block.append("- Tavily 公开信源补全：")
                if answer:
                    block.append(f"  - 一句话总结：{answer[:300]}")
                for j, r in enumerate(results[:3], 1):
                    host = (r.get("url", "") or "").split("/")[2] if "://" in (r.get("url", "") or "") else "?"
                    content = (r.get("content") or "")[:300]
                    block.append(f"  - 来源 {j} [{host}]：{content}")
        article_blocks.append("\n".join(block))

    user_parts = [
        f"## 用户已勾选的 {len(articles)} 条素材（用户自己看过，不需要复述）",
        "",
        "\n\n".join(article_blocks),
    ]

    if user_note and user_note.strip():
        user_parts.extend([
            "",
            "## 用户的额外补充（用户提供的判断主线，请围绕此展开）",
            "",
            user_note.strip(),
        ])
    else:
        user_parts.extend([
            "",
            "## 用户没有提供额外补充",
            "",
            "请你自己从这些素材里提取一条主线判断，写出深度洞察。",
        ])

    user_parts.extend([
        "",
        "## 现在请你输出",
        "",
        INSIGHT_INSTRUCTION,
    ])

    return system, "\n".join(user_parts)


# ============================================================
# 主接口
# ============================================================

# 模型配置（用户敲定 2026-06-02）
MODEL_CHOICES = {
    "reasoner": {
        "model": "deepseek-reasoner",
        "max_tokens": 3000,
        "name": "深度模式（reasoner）",
        "desc": "笔法最佳，用于重要批次",
    },
    "chat": {
        "model": "deepseek-chat",
        "max_tokens": 1500,
        "name": "快速模式（chat）",
        "desc": "速度快、成本低，日常使用",
    },
}


def generate_tip(
    *,
    articles: list[TipArticle],
    user_note: str | None = None,
    model_choice: str = "reasoner",
    temperature: float = 0.6,
    do_search: bool = True,
    cfg: LLMConfig | None = None,
) -> TipResult:
    """生成一段小 tips 洞察。
    
    Args:
        articles: 用户勾选的文章列表（已是 TipArticle 对象）
        user_note: 用户的可选补充（None 或空串都视为"无补充"）
        model_choice: "reasoner" / "chat"
        temperature: 默认 0.6（鼓励笔法多样性）
        do_search: 是否对每条文章调 Tavily（默认 True，用户敲定的策略）
        cfg: LLM 配置；不传则从 .env 自动加载

    Returns:
        TipResult。失败时 error 字段非空，tip_markdown 为空字符串。
    """
    t0 = time.monotonic()

    # 解析模型选择
    if model_choice not in MODEL_CHOICES:
        model_choice = "reasoner"
    spec = MODEL_CHOICES[model_choice]

    # 准备 LLM cfg
    if cfg is None:
        cfg = LLMConfig.from_env()

    if not articles:
        return TipResult(
            tip_markdown="",
            model=spec["model"],
            elapsed_s=0.0,
            search_log=[],
            user_note_used=False,
            n_articles=0,
            n_searches=0,
            prompt_chars=0,
            output_chars=0,
            error="没有勾选任何文章",
        )

    # ---- 1. Tavily 搜索（按用户策略：每条都搜）----
    enrichments: dict[int, dict] = {}
    search_log: list[dict] = []
    n_searches = 0

    if do_search:
        for a in articles:
            query = extract_query_for_tavily(a)
            log_entry = {
                "article_id": a.id,
                "title": a.title[:80],
                "query": query[:200],
                "ok": False,
                "n_results": 0,
                "elapsed_ms": 0,
                "error": None,
            }
            try:
                t_search = time.monotonic()
                data = tavily_search(query)
                log_entry["elapsed_ms"] = int((time.monotonic() - t_search) * 1000)
                log_entry["ok"] = True
                log_entry["n_results"] = len(data.get("results") or [])
                enrichments[a.id] = data
                n_searches += 1
            except Exception as e:
                log_entry["error"] = f"{type(e).__name__}: {str(e)[:200]}"
                enrichments[a.id] = {"error": str(e), "results": [], "answer": ""}
            search_log.append(log_entry)

    # ---- 2. 注入笔法 + 拼 prompt ----
    style_text = load_style_samples()
    system, user_msg = build_prompt(articles, enrichments, user_note, style_text)
    user_note_used = bool(user_note and user_note.strip())

    # ---- 3. 调 LLM ----
    try:
        content = chat_completion(
            cfg,
            system=system,
            user=user_msg,
            temperature=temperature,
            max_tokens=spec["max_tokens"],
            response_format_json=False,
            stage="tips_generator",
            model_override=spec["model"],
        )
    except Exception as e:
        return TipResult(
            tip_markdown="",
            model=spec["model"],
            elapsed_s=time.monotonic() - t0,
            search_log=search_log,
            user_note_used=user_note_used,
            n_articles=len(articles),
            n_searches=n_searches,
            prompt_chars=len(system) + len(user_msg),
            output_chars=0,
            error=f"LLM 调用失败：{type(e).__name__}: {str(e)[:300]}",
        )

    return TipResult(
        tip_markdown=content,
        model=spec["model"],
        elapsed_s=time.monotonic() - t0,
        search_log=search_log,
        user_note_used=user_note_used,
        n_articles=len(articles),
        n_searches=n_searches,
        prompt_chars=len(system) + len(user_msg),
        output_chars=len(content),
        error=None,
    )
