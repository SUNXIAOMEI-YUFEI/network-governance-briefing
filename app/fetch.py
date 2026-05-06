"""真 RSS 抓取（取代 fetch_mock.py）。

支持 RSS 2.0 和 Atom 1.0 两种格式，纯 stdlib，不引 feedparser/requests。
- 并发抓 RSS_FEEDS 列表里所有 feed
- 解析 entry：title / url / summary / published
- 去重：按 URL（articles.url UNIQUE 约束）
- KtN 噪声过滤：(KtN) 信源里的事务邮件（welcome/activate/...）丢弃
- 失败 feed：打印警告但不阻塞其他 feed

用法：
    python3 -m app.fetch                # 抓所有 feed，过去 24h（默认）
    python3 -m app.fetch --hours 72     # 抓过去 72 小时
    python3 -m app.fetch --hours 360    # 抓过去 15 天（首次跑、灌满库时用）
    python3 -m app.fetch --only ICO,FTC # 只抓指定信源（按 source name 模糊匹配）
"""
from __future__ import annotations

import argparse
import gzip
import io
import re
import sqlite3
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable

from app.config import (
    DB_PATH,
    FETCH_CONCURRENCY,
    HTTP_TIMEOUT_SEC,
    HTTP_USER_AGENT,
    KTN_NOISE_PATTERNS,
    RSS_FEEDS,
    SOURCE_AUTHORITY,
)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class FetchedArticle:
    url: str
    title: str
    summary: str
    source_name: str
    source_tier: str
    published_at: str   # ISO 8601 UTC


@dataclass
class FeedResult:
    source_name: str
    feed_url: str
    success: bool
    article_count: int
    error: str | None = None


# ============================================================
# HTTP & 解析
# ============================================================

ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": HTTP_USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
        raw = resp.read()
        # 解压（很多 feed 默认 gzip 返回）
        encoding = resp.headers.get("Content-Encoding", "").lower()
        if encoding == "gzip":
            raw = gzip.decompress(raw)
        elif encoding == "deflate":
            import zlib
            raw = zlib.decompress(raw)
        return raw


def _strip_html(html: str, max_len: int = 600) -> str:
    """粗暴去 HTML 标签（不引 BeautifulSoup）。够给 LLM 当 summary 用。"""
    # 把 br/p 转换行
    html = re.sub(r"<\s*br\s*/?\s*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</\s*p\s*>", "\n", html, flags=re.IGNORECASE)
    # 删除 <script>/<style> 整段
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.IGNORECASE | re.DOTALL)
    # 删 HTML 标签
    text = re.sub(r"<[^>]+>", " ", html)
    # HTML 实体
    text = (text
            .replace("&nbsp;", " ").replace("&amp;", "&")
            .replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&#39;", "'"))
    # 多空白合并
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def _parse_pub_date(raw: str) -> str:
    """把各种乱七八糟的发布时间格式统一到 ISO 8601 UTC。"""
    if not raw:
        return datetime.now(timezone.utc).isoformat()
    raw = raw.strip()
    # RSS 2.0：RFC 2822（"Mon, 06 May 2026 08:00:00 GMT"）
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    # Atom：ISO 8601（已经标准）
    try:
        # Python 3.11+ 直接 fromisoformat 支持 Z
        s = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass
    # 兜底：当前时间
    return datetime.now(timezone.utc).isoformat()


def _parse_feed(xml_bytes: bytes, source_name: str, source_tier: str) -> list[FetchedArticle]:
    """解析一个 feed（RSS 2.0 或 Atom 1.0），返回文章列表。"""
    text = xml_bytes.decode("utf-8", errors="replace")

    # 防御性：偶尔有 feed 顶部带 BOM 或非法字符
    text = text.lstrip("\ufeff")

    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise ValueError(f"XML parse error: {e}") from e

    articles: list[FetchedArticle] = []

    # --- RSS 2.0：<rss><channel><item>... ---
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not link:
            # 极少数 RSS 把 link 放在 <guid isPermaLink="true">
            guid = item.find("guid")
            if guid is not None and (guid.get("isPermaLink") in (None, "true", "True")):
                link = (guid.text or "").strip()
        if not title or not link:
            continue
        # description / content:encoded 二选一
        desc = item.findtext("description") or ""
        if not desc:
            for child in item:
                if child.tag.endswith("encoded"):
                    desc = child.text or ""
                    break
        summary = _strip_html(desc)
        pub_raw = item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date") or ""
        pub_iso = _parse_pub_date(pub_raw)

        articles.append(FetchedArticle(
            url=link, title=title, summary=summary,
            source_name=source_name, source_tier=source_tier,
            published_at=pub_iso,
        ))

    # --- Atom 1.0：<feed><entry>... ---
    for entry in root.iter(ATOM_NS + "entry"):
        title_el = entry.find(ATOM_NS + "title")
        title = (title_el.text or "").strip() if title_el is not None else ""

        # link rel=alternate（带 href）
        link = ""
        for link_el in entry.findall(ATOM_NS + "link"):
            rel = link_el.get("rel", "alternate")
            if rel == "alternate" and link_el.get("href"):
                link = link_el.get("href").strip()
                break
        if not link:
            id_el = entry.find(ATOM_NS + "id")
            if id_el is not None and (id_el.text or "").startswith("http"):
                link = (id_el.text or "").strip()

        if not title or not link:
            continue

        # summary / content
        desc = ""
        for tag in ("summary", "content"):
            el_node = entry.find(ATOM_NS + tag)
            if el_node is not None:
                desc = "".join(el_node.itertext())
                if desc:
                    break
        summary = _strip_html(desc)

        pub_el = entry.find(ATOM_NS + "published") or entry.find(ATOM_NS + "updated")
        pub_iso = _parse_pub_date(pub_el.text if pub_el is not None else "")

        articles.append(FetchedArticle(
            url=link, title=title, summary=summary,
            source_name=source_name, source_tier=source_tier,
            published_at=pub_iso,
        ))

    return articles


def _is_ktn_noise(source_name: str, title: str) -> bool:
    if "(KtN)" not in source_name:
        return False
    t = title.lower()
    return any(p in t for p in KTN_NOISE_PATTERNS)


# KtN 转发 newsletter 的通用标题模式（这些不是废邮件，但标题信息量太少，需要从 summary 里提取议题）
_KTN_GENERIC_TITLES = [
    "new from dataguidance",
    "dataguidance collections",
    "daily briefing",
    "daily digest",
    "newsletter",
    "weekly roundup",
]


def _needs_title_augmentation(source_name: str, title: str) -> bool:
    """判断是否要用 summary 补 title（KtN 专属情况）。"""
    if "(KtN)" not in source_name:
        return False
    t = title.lower().strip()
    return any(p in t for p in _KTN_GENERIC_TITLES) or len(t) < 12


def _augment_title_from_summary(title: str, summary: str) -> str:
    """把 summary 的前若干字拼到 title 前，让 LLM 能看到真议题。"""
    if not summary:
        return title
    # 取前 180 字符
    hint = summary.strip()[:180].replace("\n", " ")
    if len(summary) > 180:
        hint += "…"
    return f"{title} | {hint}"


# ============================================================
# 单 feed 抓取
# ============================================================

def fetch_one_feed(source_name: str, feed_url: str, source_tier: str) -> tuple[list[FetchedArticle], FeedResult]:
    try:
        raw = _http_get(feed_url)
    except urllib.error.HTTPError as e:
        return [], FeedResult(source_name, feed_url, False, 0, f"HTTP {e.code}")
    except urllib.error.URLError as e:
        return [], FeedResult(source_name, feed_url, False, 0, f"URL error: {e.reason}")
    except (TimeoutError, OSError) as e:
        return [], FeedResult(source_name, feed_url, False, 0, f"{type(e).__name__}: {e}")

    try:
        articles = _parse_feed(raw, source_name, source_tier)
    except ValueError as e:
        return [], FeedResult(source_name, feed_url, False, 0, str(e))

    # KtN 噪声过滤
    if "(KtN)" in source_name:
        articles = [a for a in articles if not _is_ktn_noise(source_name, a.title)]
        # KtN 通用标题（如 "New from DataGuidance collections"）→ 从 summary 拼真议题
        for a in articles:
            if _needs_title_augmentation(source_name, a.title):
                a.title = _augment_title_from_summary(a.title, a.summary)

    return articles, FeedResult(source_name, feed_url, True, len(articles))


# ============================================================
# 主流程
# ============================================================

def _record_feed_health(
    conn: sqlite3.Connection,
    results: list[FeedResult],
    feeds: list[tuple[str, str, str]],
) -> None:
    """把这轮 fetch 的成败写入 feed_health（upsert）和 feed_health_log（append）。

    - feed_health：每个 feed 一行，只存"当前状态"（最近一次）
    - feed_health_log：每次 fetch 的结果 append 一条，用于算近 7 天成功率
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    # feed 基础信息（url/tier）从入参 feeds 里拿，避免每次都查 RSS_FEEDS
    feed_meta = {name: (url, tier) for (name, url, tier) in feeds}

    for r in results:
        feed_url, tier = feed_meta.get(r.source_name, (r.feed_url, None))

        # 1) 写日志
        conn.execute(
            """
            INSERT INTO feed_health_log
                (source_name, attempted_at, success, article_count, error)
            VALUES (?, ?, ?, ?, ?)
            """,
            (r.source_name, now_iso, 1 if r.success else 0, r.article_count, r.error),
        )

        # 2) upsert 当前状态
        row = conn.execute(
            "SELECT consecutive_fails FROM feed_health WHERE source_name = ?",
            (r.source_name,),
        ).fetchone()
        prev_fails = row[0] if row else 0

        if r.success:
            new_fails = 0
            conn.execute(
                """
                INSERT INTO feed_health
                    (source_name, feed_url, source_tier, last_attempt_at,
                     last_success_at, last_error, last_article_count, consecutive_fails)
                VALUES (?, ?, ?, ?, ?, NULL, ?, 0)
                ON CONFLICT(source_name) DO UPDATE SET
                    feed_url = excluded.feed_url,
                    source_tier = excluded.source_tier,
                    last_attempt_at = excluded.last_attempt_at,
                    last_success_at = excluded.last_success_at,
                    last_error = NULL,
                    last_article_count = excluded.last_article_count,
                    consecutive_fails = 0
                """,
                (r.source_name, feed_url, tier, now_iso, now_iso, r.article_count),
            )
        else:
            new_fails = prev_fails + 1
            conn.execute(
                """
                INSERT INTO feed_health
                    (source_name, feed_url, source_tier, last_attempt_at,
                     last_success_at, last_error, last_article_count, consecutive_fails)
                VALUES (?, ?, ?, ?, NULL, ?, 0, ?)
                ON CONFLICT(source_name) DO UPDATE SET
                    feed_url = excluded.feed_url,
                    source_tier = excluded.source_tier,
                    last_attempt_at = excluded.last_attempt_at,
                    last_error = excluded.last_error,
                    consecutive_fails = feed_health.consecutive_fails + 1
                """,
                (r.source_name, feed_url, tier, now_iso, r.error, new_fails),
            )

    # 清理 14 天前的日志，避免表无限增长
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    conn.execute("DELETE FROM feed_health_log WHERE attempted_at < ?", (cutoff,))


def _filter_feeds(only: list[str] | None) -> list[tuple[str, str, str]]:
    if not only:
        return list(RSS_FEEDS)
    keys = [s.lower() for s in only]
    return [
        (n, u, t) for (n, u, t) in RSS_FEEDS
        if any(k in n.lower() for k in keys)
    ]


def fetch_all(
    *,
    hours: int = 24,
    only: list[str] | None = None,
    concurrency: int = FETCH_CONCURRENCY,
) -> None:
    feeds = _filter_feeds(only)
    if not feeds:
        print("[fetch] 没有匹配的 feed")
        return

    print(f"[fetch] 抓取 {len(feeds)} 个 feed，过去 {hours} 小时，并发={concurrency}")

    # ---- 并发抓 ----
    all_articles: list[FetchedArticle] = []
    results: list[FeedResult] = []

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(fetch_one_feed, name, url, tier): (name, url)
            for (name, url, tier) in feeds
        }
        for fut in as_completed(futures):
            name, url = futures[fut]
            try:
                articles, result = fut.result()
                results.append(result)
                all_articles.extend(articles)
            except Exception as e:  # noqa: BLE001
                results.append(FeedResult(name, url, False, 0, f"unexpected: {e}"))

    # ---- 时间窗过滤 ----
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    in_window: list[FetchedArticle] = []
    for art in all_articles:
        try:
            dt = datetime.fromisoformat(art.published_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                in_window.append(art)
        except ValueError:
            # 解析失败的也保留（避免错过新闻）
            in_window.append(art)

    # ---- 入库（URL UNIQUE 自动去重）----
    inserted, skipped = 0, 0
    with sqlite3.connect(DB_PATH) as conn:
        for art in in_window:
            # 信源档次：以 SOURCE_AUTHORITY 为准（feed config 里的 tier 是兜底）
            tier = SOURCE_AUTHORITY.get(art.source_name, art.source_tier)
            try:
                conn.execute(
                    """
                    INSERT INTO articles
                        (url, title, summary, source_name, source_tier, published_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (art.url, art.title, art.summary, art.source_name, tier, art.published_at),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1

        # ---- 写入信源健康度（每个 feed 一条 upsert + 一条日志）----
        _record_feed_health(conn, results, feeds)

        conn.commit()

    # ---- 报告 ----
    success_count = sum(1 for r in results if r.success)
    fail_count = len(results) - success_count
    total_in_feeds = sum(r.article_count for r in results)

    print(f"[fetch] feed 抓取：成功 {success_count} / 失败 {fail_count}（共 {total_in_feeds} 条）")
    print(f"[fetch] 时间窗内：{len(in_window)} 条")
    print(f"[fetch] 入库新增：{inserted} 条；URL 重复跳过：{skipped} 条")

    if fail_count > 0:
        print("\n[fetch] ⚠️ 失败的 feed（不阻塞流水线）：")
        for r in results:
            if not r.success:
                print(f"  - {r.source_name:30s}  {r.error}")
                print(f"    {r.feed_url}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24, help="时间窗（小时），默认 24")
    parser.add_argument("--only", type=str, default="",
                        help="逗号分隔的信源名子串过滤，如 'ICO,FTC,KtN'")
    parser.add_argument("--concurrency", type=int, default=FETCH_CONCURRENCY)
    args = parser.parse_args()

    only = [s.strip() for s in args.only.split(",") if s.strip()] if args.only else None
    fetch_all(hours=args.hours, only=only, concurrency=args.concurrency)
    return 0


if __name__ == "__main__":
    sys.exit(main())
