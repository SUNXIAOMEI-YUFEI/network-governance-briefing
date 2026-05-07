"""v1.4：按时间窗的 LLM 主题聚类

输入：某个时间窗内所有通过评分（非 veto 且 total_score > 某阈值）的文章
输出：3-6 个主题簇，每个簇含：
  - emoji + 中文主题名（如 🤖 Agent 智能体治理）
  - 主题一句话说明
  - 该主题下的文章 id 列表（按总分降序）

实现：
  - 把文章的 (id, title_cn, source, 短摘要) 聚成 JSON，一次 LLM 调用让它分组
  - temperature=0 保证稳定；如果 LLM 抽风分错，下次跑会重算（每天只跑 1 次）
  - LLM 调用频次：4 个时间窗 × 每天 1 次 = 4 次/天，成本忽略
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import DB_PATH
from app.llm_client import LLMConfig, LLMError, chat_completion, extract_json


# ============================================================
# 数据准备：从 DB 捞某时间窗的合格文章
# ============================================================

@dataclass
class ArticleBrief:
    id: int
    title_cn: str
    title: str
    source_name: str
    content_type: str
    total_score: int
    summary_short: str


def load_articles_in_window(hours: int, *, min_score: int = 40, limit: int = 60) -> list[ArticleBrief]:
    """捞过去 N 小时内：非 veto + total_score >= min_score 的文章，按分降序。

    min_score=40 过滤掉边缘文章（LLM 聚类时噪声少）
    limit=60 保护 prompt 不会太长
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, title_cn, title, source_name, content_type, total_score, summary
            FROM articles
            WHERE veto IS NULL
              AND total_score >= ?
              AND published_at >= ?
            ORDER BY total_score DESC
            LIMIT ?
            """,
            (min_score, cutoff, limit),
        ).fetchall()

    out: list[ArticleBrief] = []
    for r in rows:
        title_cn = (r["title_cn"] or "").strip() or r["title"]
        summary = (r["summary"] or "")[:200].replace("\n", " ").strip()
        out.append(ArticleBrief(
            id=r["id"], title_cn=title_cn, title=r["title"],
            source_name=r["source_name"], content_type=r["content_type"],
            total_score=r["total_score"], summary_short=summary,
        ))
    return out


# ============================================================
# LLM 主题聚类
# ============================================================

_SYSTEM_PROMPT = """你是资深网络治理研究员，正在帮一份内部情报简报做"当期热点主题聚类"。

输入：某个时间窗（如过去 24 小时）内已评分合格的 N 篇文章列表，每条有 id / 中文标题 / 信源 / 分数。

任务：把这些文章归纳成 **3-6 个热点主题**，每个主题用一个关键词概括（如"Agent 智能体治理""未成年人保护""平台反垄断 DMA"）。

要求：
1. 主题命名用**中文短语** 8-14 字，可带 1 个 emoji 开头（🤖/👶/🔐/⚖️/📢/🛰️ 等）
2. 同主题下文章要**真相关**；不确定的文章归入"🔀 其他治理动态"兜底主题
3. 每个主题至少 1 篇、最多 10 篇
4. 主题之间**不交叉**——每篇文章只能归入一个主题
5. 主题按**文章总数降序**排列
6. 主题后面加一句**简短说明**（15 字内）

输出严格 JSON：
```json
{
  "topics": [
    {
      "emoji": "🤖",
      "name": "Agent 智能体治理",
      "blurb": "监管者针对 AI Agent 新形态的规则动态",
      "article_ids": [12, 45, 78]
    },
    {
      "emoji": "👶",
      "name": "未成年人网络保护",
      "blurb": "针对未成年人接触 AI/网络内容的立法",
      "article_ids": [3, 56]
    }
  ]
}
```

article_ids 必须是输入里真实存在的 id，不要编造。
只输出 JSON，不加任何 markdown 代码块标记或前后说明。"""


def cluster_by_llm(articles: list[ArticleBrief], *, cfg: LLMConfig | None = None) -> list[dict[str, Any]]:
    """调 LLM 把一批文章归纳成主题。返回 topics 列表（不含 articles 详情，只含 article_ids）。"""
    if not articles:
        return []
    if len(articles) <= 3:
        # 文章太少，不做 LLM 聚类，直接单主题兜底
        return [{
            "emoji": "📌",
            "name": "当期要点",
            "blurb": "候选文章较少，未做主题归纳",
            "article_ids": [a.id for a in articles],
        }]

    cfg = cfg or LLMConfig.from_env()

    # 拼输入列表
    rows = []
    for a in articles:
        rows.append({
            "id": a.id,
            "title": a.title_cn,
            "source": a.source_name,
            "score": a.total_score,
            "type": a.content_type,
            "summary": a.summary_short[:150],
        })

    user_msg = (
        f"以下是 {len(rows)} 篇待聚类文章（JSON），按分数降序。\n\n"
        f"```json\n{json.dumps(rows, ensure_ascii=False, indent=2)}\n```\n\n"
        "请按上面规范归纳 3-6 个主题，严格输出 JSON。"
    )

    try:
        raw = chat_completion(
            cfg,
            system=_SYSTEM_PROMPT,
            user=user_msg,
            temperature=0.0,
            max_tokens=1500,
            response_format_json=True,
        )
        obj = extract_json(raw)
    except LLMError as e:
        print(f"[topic_cluster] LLM 调用失败：{e}")
        return []

    topics = obj.get("topics") or []
    if not isinstance(topics, list):
        return []

    # 清洗 & 验证
    valid_ids = {a.id for a in articles}
    cleaned: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    for t in topics:
        if not isinstance(t, dict):
            continue
        name = (t.get("name") or "").strip()
        if not name:
            continue
        ids = t.get("article_ids") or []
        if not isinstance(ids, list):
            continue
        # 过滤不存在的 id + 去重
        clean_ids: list[int] = []
        for i in ids:
            try:
                ii = int(i)
            except (TypeError, ValueError):
                continue
            if ii in valid_ids and ii not in seen_ids:
                clean_ids.append(ii)
                seen_ids.add(ii)
        if not clean_ids:
            continue
        cleaned.append({
            "emoji": (t.get("emoji") or "📌").strip()[:4],
            "name": name[:30],
            "blurb": (t.get("blurb") or "").strip()[:60],
            "article_ids": clean_ids,
        })

    # 未分配的文章兜底归入"🔀 其他治理动态"
    orphan_ids = [a.id for a in articles if a.id not in seen_ids]
    if orphan_ids:
        cleaned.append({
            "emoji": "🔀",
            "name": "其他治理动态",
            "blurb": "未归入主类的候选选题",
            "article_ids": orphan_ids,
        })

    return cleaned


# ============================================================
# 按时间窗批量生成 topics
# ============================================================

def build_topics_by_window(time_windows: dict[str, int], *,
                           cfg: LLMConfig | None = None,
                           min_score: int = 40) -> dict[str, list[dict[str, Any]]]:
    """为每个时间窗各跑一次 LLM 聚类。

    返回：{"24h": [topic,...], "72h": [...], ...}
    """
    out: dict[str, list[dict[str, Any]]] = {}
    cfg = cfg or LLMConfig.from_env()

    for tab, hours in time_windows.items():
        articles = load_articles_in_window(hours, min_score=min_score)
        if not articles:
            out[tab] = []
            continue
        print(f"[topic_cluster] {tab}: 输入 {len(articles)} 篇 → LLM 聚类中...")
        topics = cluster_by_llm(articles, cfg=cfg)
        print(f"[topic_cluster] {tab}: 生成 {len(topics)} 个主题")
        for t in topics:
            print(f"  {t['emoji']} {t['name']} ({len(t['article_ids'])} 篇)")
        out[tab] = topics

    return out


if __name__ == "__main__":
    from app.config import DB_PATH as _dp  # noqa
    from app.build_today import TIME_WINDOWS
    import os

    env_file = _dp.parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    result = build_topics_by_window(TIME_WINDOWS)
    print("\n=== 最终输出 ===")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:3000])
