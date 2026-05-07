"""文章评分模块。

提供两种 scorer：
- MockScorer：基于关键词启发式，无需任何 API key，用于跑通流水线
- ClaudeScorer：调真 Claude API（Step 4 接入）

调用方式：
    python3 -m app.score                # 默认 mock 模式
    python3 -m app.score --real-llm     # 真 LLM（需要 ANTHROPIC_API_KEY）

行为：
1. 从 articles 表读出所有"未评分"的文章（total_score IS NULL）
2. 逐条评分，写回 6 维分数 + content_type + 议题指纹 + veto + 焦虑点
3. C 维度由 source_tier 直接映射（不让 LLM 打）
4. D 维度此阶段先填 0，等 cluster.py 聚类完再回填
5. total_score = A + B + C + D + E + F（如果 veto != null 则 = 0）
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from app.config import (
    ANXIETY_HIGH_WEIGHT,
    ANXIETY_KEYWORDS,
    ANXIETY_MID_WEIGHT,
    APP_DIR,
    CONTENT_TYPE_SIGNALS,
    DB_PATH,
    DOMESTIC_CAC_BLACKLIST,
    INDUSTRY_BLACKLIST,
    MATURITY_SIGNALS,
    TIER_TO_SCORE,
)
from app.llm_client import LLMConfig, LLMError, chat_completion, extract_json


# ============================================================
# 数据结构
# ============================================================

@dataclass
class Article:
    id: int
    url: str
    title: str
    summary: str
    source_name: str
    source_tier: str
    published_at: str


@dataclass
class ScoreResult:
    score_a: int
    score_b: int
    score_e: int
    score_f: int
    fingerprint: str
    veto: str | None  # None / "1" / "2" / "3" / "5" / "6"
    anxiety_hits: list[str]
    maturity_stage: str
    reason: str
    content_type: str
    content_type_reason: str
    title_cn: str = ""


# ============================================================
# Scorer 接口
# ============================================================

class ArticleScorer(ABC):
    @abstractmethod
    def score(self, article: Article) -> ScoreResult: ...


# ============================================================
# Mock 评分（关键词启发式，用于 Step 2 跑通流水线）
# ============================================================

class MockScorer(ArticleScorer):
    """基于关键词的启发式评分。

    不是要替代真 LLM，只是为了让"流水线 + 双栏布局 + 聚类"先跑起来，
    便于用户验收输出端的视觉效果。
    """

    def score(self, article: Article) -> ScoreResult:
        text = f"{article.title} {article.summary}".lower()

        # ---------- 一票否决判断 ----------
        veto = self._detect_veto(text, article)

        # ---------- A 维度：8 大焦虑点 ----------
        anxiety_hits, score_a = self._score_anxiety(text)

        # ---------- B 维度：议题成熟度 ----------
        maturity_stage, score_b = self._score_maturity(text)

        # ---------- E 维度：产业可借鉴性 ----------
        score_e = self._score_borrowability(text, anxiety_hits)

        # ---------- F 维度：稀缺性 ----------
        # mock 里没法真查中文世界，按信源档次反推：S/A 级新闻通常中文世界跟得慢
        score_f = self._score_scarcity(article.source_tier)

        # ---------- v1.1：content_type 判断 ----------
        content_type, content_type_reason = self._detect_content_type(text, article)

        # ---------- 议题指纹 ----------
        fingerprint = self._build_fingerprint(article, text)

        # ---------- 一句话理由 ----------
        reason = self._build_reason(article, anxiety_hits, maturity_stage, content_type, veto)

        return ScoreResult(
            score_a=score_a,
            score_b=score_b,
            score_e=score_e,
            score_f=score_f,
            fingerprint=fingerprint,
            veto=veto,
            anxiety_hits=anxiety_hits,
            maturity_stage=maturity_stage,
            reason=reason,
            content_type=content_type,
            content_type_reason=content_type_reason,
            title_cn=article.title,  # mock 桩不翻译，用原英文
        )

    # -------- 私有 helper --------

    def _detect_veto(self, text: str, article: Article) -> str | None:
        # #6 产业政策黑名单
        for kw in INDUSTRY_BLACKLIST:
            if kw.lower() in text:
                return "6"
        # #5 国内网信办执法
        for kw in DOMESTIC_CAC_BLACKLIST:
            if kw.lower() in text:
                return "5"
        # #1/#2/#3 这一版 mock 不严格区分（真 LLM 才能判得准），先放过
        return None

    def _score_anxiety(self, text: str) -> tuple[list[str], int]:
        hits: list[str] = []
        for anxiety, kws in ANXIETY_KEYWORDS.items():
            if any(kw.lower() in text for kw in kws):
                hits.append(anxiety)
        if not hits:
            return [], 0
        # 命中高权重 → 满分 30；只命中中权重 → 20；混合 → 取最高档
        if any(h in ANXIETY_HIGH_WEIGHT for h in hits):
            return hits, 28
        if any(h in ANXIETY_MID_WEIGHT for h in hits):
            return hits, 18
        return hits, 5

    def _score_maturity(self, text: str) -> tuple[str, int]:
        # 优先级：落地执行 > 规则成形 > 讨论立法 > 风险冒头
        stage_score = {"落地执行期": 3, "规则成形期": 8, "讨论立法期": 15, "风险冒头期": 18}
        for stage in ("落地执行期", "规则成形期", "讨论立法期"):
            for kw in MATURITY_SIGNALS[stage]:
                if kw.lower() in text:
                    return stage, stage_score[stage]
        return "风险冒头期", stage_score["风险冒头期"]

    def _score_borrowability(self, text: str, anxiety_hits: list[str]) -> int:
        # 启发式：含未成年/算法/标识等"中国也想抄"的议题给高分；
        # 含具体监管动作（fine/penalty）= 利好（让中国监管也能跟进）；
        # 否则中性 5。
        if not anxiety_hits:
            return 3
        if any(s in text for s in ("fine", "penalty", "guidance", "rule", "code of practice")):
            return 9
        return 6

    def _score_scarcity(self, tier: str) -> int:
        # 启发式：S/A 级英文官方源 → 中文世界很少跟；C/D → 大概率有中文报道
        return {"S": 9, "A": 8, "B": 5, "C": 3, "D": 2}.get(tier, 5)

    def _detect_content_type(self, text: str, article: Article) -> tuple[str, str]:
        # ---- 律所/官方源兜底：优先于关键词启发式 ----
        # 律所 alert 99% 是事实陈述（介绍法律 / 描述执法案件），即使含 opinion 词
        # （这一兜底真 LLM 阶段不需要，纯 mock 用）
        src = article.source_name.lower()
        if "covington" in src or "inside privacy" in src:
            # Covington Inside Privacy 通常介绍法律规定 → 立法事实
            # 但若标题含执法关键词，归执法
            if any(kw in text for kw in ("fines", "fined", "ruling", "investigation", "enforcement")):
                return "fact_enforcement", "Inside Privacy（Covington）alert，标题含执法信号"
            return "fact_legislative", "Inside Privacy（Covington）律所 alert，主体为法律规定介绍"
        if "hogan" in src or "wilmerhale" in src:
            if any(kw in text for kw in ("fines", "fined", "ruling", "investigation", "enforcement", "wave of")):
                return "fact_enforcement", "Hogan/WilmerHale alert，主体为执法事实陈述"
            return "fact_legislative", "Hogan/WilmerHale alert，主体为法律规定介绍"
        # 监管者/标准组织（NIST、ICO、FTC、Commission、Garante、CNIL、EDPB...）
        # 它们发布的内容默认归 fact_official_doc，除非命中执法/立法关键词更强信号
        REGULATORS = ("nist", "ico", "ftc", "european commission", "garante", "cnil",
                      "edpb", "ofcom", "doj", "white house")
        if any(reg in src for reg in REGULATORS):
            if any(kw in text for kw in ("fines", "fined", "settlement", "ruling")):
                return "fact_enforcement", f"{article.source_name} 监管者发布执法相关动态"
            if any(kw in text for kw in ("signed", "enacted", "into law", "final rule",
                                          "finalizes", "finalises", "rule update")):
                return "fact_legislative", f"{article.source_name} 监管者发布立法相关动态"
            return "fact_official_doc", f"{article.source_name} 监管者发布官方文件/指南/报告"

        # ---- 否则走关键词启发式 ----
        scores = {ct: 0 for ct in CONTENT_TYPE_SIGNALS}
        for ct, signals in CONTENT_TYPE_SIGNALS.items():
            for sig in signals:
                if sig.lower() in text:
                    scores[ct] += 1

        max_score = max(scores.values())
        if max_score == 0:
            return "opinion_analysis", "无明显事实/观点信号词，按 v1.1 默认归类"

        # 取得分最高的类别（多个并列时按内置优先级：fact_* 优先于 opinion）
        priority = ["fact_legislative", "fact_enforcement", "fact_official_doc", "opinion_analysis"]
        winners = [ct for ct in priority if scores[ct] == max_score]
        winner = winners[0]

        reasons = {
            "fact_legislative":  "命中立法事实信号（signed/enacted/effective/passed 等）",
            "fact_enforcement":  "命中执法事实信号（fines/ruling/settlement/investigation 等）",
            "fact_official_doc": "命中官方文件信号（publishes/issues guidance/RFI 等）",
            "opinion_analysis":  "命中观点分析信号（why/argues/the case for 等）",
        }
        return winner, reasons[winner]

    def _build_fingerprint(self, article: Article, text: str) -> str:
        """启发式 fingerprint：从标题+摘要里抓关键 token，组合成稳定 ID。

        真 LLM 阶段会由 Claude 直接输出 fingerprint，更精准。
        """
        # 几个手工映射的高频议题（保证 mock 数据里同议题文章 fingerprint 一致）
        title_lower = article.title.lower()
        if "gpai code of practice" in title_lower or "gpai code" in text:
            return "EU-AI-Act-GPAI-CodeOfPractice-2026-05"
        if "garante" in text and "openai" in text:
            return "IT-Garante-OpenAI-GDPR-Fine-2026-05"
        if "coppa" in title_lower:
            return "US-FTC-COPPA-Update-2026-04"
        if "age assurance" in text:
            return "UK-ICO-AgeAssurance-Guidance-2026-05"
        if "ai rmf" in text or "generative ai profile" in text:
            return "US-NIST-AIRMF-GenAI-Profile-2026-04"
        if "sb 1047" in title_lower or "ai companion" in text:
            return "US-CA-SB1047-AICompanion-2026-05"
        if "agent" in title_lower and ("liability" in text or "lawsuit" in text):
            return "Global-AgenticAI-Liability-2026-Q2"
        if "sb 2420" in title_lower or "sb2420" in text:
            return "US-TX-SB2420-AIDisclosure-2026-05"
        if "ofcom" in text and "illegal harms" in text:
            return "UK-Ofcom-OSA-IllegalHarms-2026-05"
        if "watermark" in text or "c2pa" in text:
            return "Global-AIWatermark-Failure-2026-05"
        if "h200" in title_lower or ("nvidia" in text and "export" in text):
            return "US-Nvidia-ExportControl-2026-05"
        if "asml" in title_lower:
            return "NL-ASML-ExportControl-2026-05"
        # 兜底：用 url 末段 + 发布日期前缀
        slug = article.url.rstrip("/").rsplit("/", 1)[-1][:40]
        return f"misc-{slug}"

    def _build_reason(
        self,
        article: Article,
        anxiety_hits: list[str],
        maturity_stage: str,
        content_type: str,
        veto: str | None,
    ) -> str:
        if veto:
            return f"命中一票否决 #{veto}（产业政策/国内执法/商业新闻/主题不相关），不进选题。"
        ax = "+".join(anxiety_hits) if anxiety_hits else "无关切议题命中"
        type_short = {
            "fact_legislative": "立法事实",
            "fact_enforcement": "执法事实",
            "fact_official_doc": "官方文件",
            "opinion_analysis": "观点分析",
        }[content_type]
        return f"[{type_short}] {article.source_name} · {maturity_stage} · {ax}"


# ============================================================
# 真 LLM 评分（OpenAI 兼容端点：OpenRouter / chr1 中转 / Claude 原生 均可）
# ============================================================

PROMPT_PATH = APP_DIR / "prompts" / "score_article.md"


def _load_prompt_template() -> tuple[str, str]:
    """从 prompts/score_article.md 抽出 system prompt 和 user 模板。

    约定（看 prompt 文件结构）：
    - '## System Prompt' 到 '## User Prompt 模板（每条文章注入）' 之间是 system
    - '## User Prompt 模板（每条文章注入）' 之后的 ``` ... ``` 代码块是 user 模板
    """
    raw = PROMPT_PATH.read_text(encoding="utf-8")

    # system：从 "## System Prompt" 到 "## User Prompt 模板" 之间
    try:
        sys_start = raw.index("## System Prompt")
        usr_header = raw.index("## User Prompt 模板")
        system_text = raw[sys_start:usr_header].split("\n", 1)[1].strip()
    except ValueError:
        # prompt 文件结构意外时，fallback 用整个文档
        system_text = raw

    # user template：取 User Prompt 那节后第一个 ``` ... ``` 块
    after = raw[usr_header:] if "usr_header" in dir() else ""
    user_tpl = ""
    if "```" in after:
        parts = after.split("```")
        # 找第一个非 language-tag 的代码块
        for i in range(1, len(parts), 2):
            block = parts[i]
            # 去掉首行可能的 language 标
            block_lines = block.split("\n")
            if block_lines and block_lines[0].strip() in ("text", "markdown", "md", ""):
                user_tpl = "\n".join(block_lines[1:]).strip()
            else:
                user_tpl = block.strip()
            if user_tpl:
                break

    if not user_tpl:
        # 兜底
        user_tpl = (
            "请评估以下文章：\n\n"
            "标题：{title}\n"
            "信源：{source_name}（档次：{source_tier}）\n"
            "发布时间：{published_at}\n"
            "摘要/正文：{summary}\n"
            "URL：{url}\n\n"
            "按 system prompt 中的规范输出严格 JSON。"
        )

    return system_text, user_tpl


class OpenAICompatScorer(ArticleScorer):
    """走 OpenAI 兼容端点的评分 scorer。

    适用任何支持 /v1/chat/completions 的服务：
    - OpenRouter
    - chr1 等中转站
    - OpenAI 官方
    - Anthropic 的 OpenAI 兼容层（如果开了）

    v1.2 升级：
    - 双评取高（self-consistency）：同一文章调用 LLM 2 次，合并策略见 score()。
    - temperature 两次分别用 0.0 / 0.3，增加第二次的探索性。
    """

    def __init__(self, cfg: LLMConfig | None = None, *, double_pass: bool = True) -> None:
        self.cfg = cfg or LLMConfig.from_env()
        self._system, self._user_tpl = _load_prompt_template()
        self.double_pass = double_pass  # 可通过 env LLM_DOUBLE_PASS=0 关掉

    def _single_call(self, article: Article, *, temperature: float) -> ScoreResult:
        user_msg = self._user_tpl.format(
            title=article.title,
            source_name=article.source_name,
            source_tier=article.source_tier,
            published_at=article.published_at,
            summary=article.summary or "(无摘要)",
            url=article.url,
        )
        raw = chat_completion(
            self.cfg,
            system=self._system,
            user=user_msg,
            temperature=temperature,
            max_tokens=1200,  # v1.2 从 800 → 1200，支持更长 reason
            response_format_json=True,
        )
        try:
            obj = extract_json(raw)
        except LLMError as e:
            raise LLMError(f"[article id={article.id}] {e}") from e

        # 字段提取（带宽容默认值，防止个别字段缺失就炸掉整批）
        scores = obj.get("scores") or {}
        score_a = _clip(scores.get("A", 0), 0, 30)
        score_b = _clip(scores.get("B", 0), 0, 20)
        score_e = _clip(scores.get("E", 0), 0, 10)
        score_f = _clip(scores.get("F", 0), 0, 10)

        veto_raw = obj.get("veto")
        veto = None if veto_raw in (None, "null", "", 0) else str(veto_raw)

        anxiety_hits = obj.get("anxiety_hits") or []
        if not isinstance(anxiety_hits, list):
            anxiety_hits = []

        maturity_stage = obj.get("maturity_stage") or "风险冒头期"
        reason = obj.get("reason") or ""

        content_type = obj.get("content_type") or "opinion_analysis"
        if content_type not in (
            "fact_legislative", "fact_enforcement", "fact_official_doc", "opinion_analysis"
        ):
            content_type = "opinion_analysis"
        content_type_reason = obj.get("content_type_reason") or ""

        fingerprint = obj.get("fingerprint") or f"misc-{article.id}"

        # v1.1+：中文归纳标题（LLM 做归纳；没给就 fallback 用英文原题）
        title_cn = (obj.get("title_cn") or "").strip()
        if not title_cn:
            title_cn = article.title

        return ScoreResult(
            score_a=score_a,
            score_b=score_b,
            score_e=score_e,
            score_f=score_f,
            fingerprint=fingerprint,
            veto=veto,
            anxiety_hits=anxiety_hits,
            maturity_stage=maturity_stage,
            reason=reason,
            content_type=content_type,
            content_type_reason=content_type_reason,
            title_cn=title_cn,
        )

    def score(self, article: Article) -> ScoreResult:
        # 第一次调用（确定性，temperature=0）
        r1 = self._single_call(article, temperature=0.0)

        if not self.double_pass:
            return r1

        # 第二次调用（小扰动，temperature=0.3）
        try:
            r2 = self._single_call(article, temperature=0.3)
        except LLMError as e:
            print(f"[score] ⚠️ 第二次评分失败（仍用第一次）id={article.id}: {e}")
            return r1

        # 合并策略：
        # 1) 两次都不 veto → 取 total 更高的（高分更可能捕捉到治理价值）
        # 2) 只有一次 veto → 信任非 veto 的那次（避免 LLM 误杀）
        # 3) 两次都 veto → 用第一次
        t1 = (r1.score_a + r1.score_b + r1.score_e + r1.score_f) if not r1.veto else -1
        t2 = (r2.score_a + r2.score_b + r2.score_e + r2.score_f) if not r2.veto else -1

        if t1 == -1 and t2 == -1:
            chosen = r1
        elif t1 == -1:
            chosen = r2
        elif t2 == -1:
            chosen = r1
        else:
            chosen = r1 if t1 >= t2 else r2

        ax_union = list({*(r1.anxiety_hits or []), *(r2.anxiety_hits or [])})
        tcn = chosen.title_cn or (r2.title_cn if chosen is r1 else r1.title_cn) or article.title

        return ScoreResult(
            score_a=chosen.score_a,
            score_b=chosen.score_b,
            score_e=chosen.score_e,
            score_f=chosen.score_f,
            fingerprint=chosen.fingerprint,
            veto=chosen.veto,
            anxiety_hits=ax_union,
            maturity_stage=chosen.maturity_stage,
            reason=chosen.reason,
            content_type=chosen.content_type,
            content_type_reason=chosen.content_type_reason,
            title_cn=tcn,
        )


def _clip(v: Any, lo: int, hi: int) -> int:
    try:
        iv = int(v)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, iv))


# 兼容旧名字：保留 ClaudeScorer 别名以免外部引用失效
ClaudeScorer = OpenAICompatScorer


# ============================================================
# 主流程
# ============================================================

def _row_to_article(row: sqlite3.Row) -> Article:
    return Article(
        id=row["id"],
        url=row["url"],
        title=row["title"],
        summary=row["summary"] or "",
        source_name=row["source_name"],
        source_tier=row["source_tier"],
        published_at=row["published_at"],
    )


def _load_protected_ids() -> set[int]:
    """v1.4：读取 data/user_favorites.json 里的文章 id 集合。

    rescore 时跳过这些文章，让用户收藏过的选题评分**永远不变**。
    """
    from app.config import DATA_DIR
    fav_file = DATA_DIR / "user_favorites.json"
    if not fav_file.exists():
        return set()
    try:
        import json as _json
        data = _json.loads(fav_file.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return set()
        ids = set()
        for f in data:
            if isinstance(f, dict) and "id" in f:
                try:
                    ids.add(int(f["id"]))
                except (TypeError, ValueError):
                    continue
        return ids
    except Exception as e:  # noqa
        print(f"[score] ⚠️ 读用户收藏保护列表失败：{e}")
        return set()


def run(scorer: ArticleScorer, *, rescore_all: bool = False, concurrency: int = 1) -> None:
    protected_ids: set[int] = _load_protected_ids() if rescore_all else set()
    if protected_ids:
        print(f"[score] 🛡️ 用户收藏保护：{len(protected_ids)} 篇收藏文章将跳过 rescore")

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if rescore_all:
            rows = conn.execute("SELECT * FROM articles").fetchall()
            # 过滤掉受保护的收藏
            if protected_ids:
                rows = [r for r in rows if r["id"] not in protected_ids]
        else:
            rows = conn.execute(
                "SELECT * FROM articles WHERE total_score IS NULL"
            ).fetchall()

        if not rows:
            print("[score] 没有需要评分的文章（带 --rescore-all 强制重打）")
            return

        articles = [_row_to_article(r) for r in rows]

        # ---- 并发评分：串行（concurrency=1）或线程池（LLM I/O bound）----
        results: list[tuple[Article, ScoreResult | Exception]] = []
        if concurrency <= 1:
            for art in articles:
                try:
                    results.append((art, scorer.score(art)))
                except Exception as e:
                    results.append((art, e))
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = {pool.submit(scorer.score, art): art for art in articles}
                for fut in as_completed(futures):
                    art = futures[fut]
                    try:
                        results.append((art, fut.result()))
                    except Exception as e:
                        results.append((art, e))

        # ---- 写库 ----
        scored = 0
        errors = 0
        veto_count = 0
        type_dist: dict[str, int] = {}

        for article, outcome in results:
            if isinstance(outcome, Exception):
                errors += 1
                print(f"[score] ⚠️ 失败 id={article.id} {article.title[:50]}: {outcome}")
                continue

            result: ScoreResult = outcome
            # C 维度：信源档次直接映射
            score_c = TIER_TO_SCORE[article.source_tier]
            # D 维度：留空，cluster.py 回填
            score_d = 0
            total = (
                0
                if result.veto
                else result.score_a + result.score_b + score_c + score_d + result.score_e + result.score_f
            )

            # === v1.2 保护：rescore 模式下防"LLM 波动降分"覆盖 ===
            # 问题背景：LLM 对同一文章两次评分可能差 10-40 分（尤其 KtN newsletter 这类
            # 通用标题邮件），rescore_all 会把原本高分的好选题推下来，用户体验崩坏。
            # 策略：只在 rescore_all 模式下，若新分数比旧分数低 ≥ SCORE_DROP_GUARD，
            # 保留旧的 6 维分数/veto/content_type/reason，只更新 title_cn（补中文标题）。
            SCORE_DROP_GUARD = 15
            old_row = conn.execute(
                "SELECT total_score, score_a, score_b, score_e, score_f, fingerprint, "
                "veto, anxiety_hits, maturity_stage, reason, content_type, content_type_reason "
                "FROM articles WHERE id = ?",
                (article.id,),
            ).fetchone()
            protected = False
            if rescore_all and old_row and old_row["total_score"] is not None:
                old_total = old_row["total_score"]
                if old_total > 0 and total > 0 and (old_total - total) >= SCORE_DROP_GUARD:
                    protected = True
                # 新评分把原来的非 veto 文章打成 veto/0 分，也视为可疑波动
                if old_total >= 50 and total == 0:
                    protected = True

            if protected:
                # 仅更新 title_cn，其余评分维度保持旧值
                print(f"[score] 🛡️ 保留旧评分 id={article.id} 旧 total={old_row['total_score']} 新 total={total}（差距过大，疑似 LLM 波动）: {article.title[:50]}")
                conn.execute(
                    "UPDATE articles SET title_cn = ? WHERE id = ?",
                    (result.title_cn or "", article.id),
                )
                scored += 1
                if old_row["veto"]:
                    veto_count += 1
                ctype = old_row["content_type"] or result.content_type
                type_dist[ctype] = type_dist.get(ctype, 0) + 1
                continue

            conn.execute(
                """
                UPDATE articles SET
                    score_a = ?, score_b = ?, score_c = ?, score_d = ?,
                    score_e = ?, score_f = ?, total_score = ?,
                    fingerprint = ?, veto = ?, anxiety_hits = ?,
                    maturity_stage = ?, reason = ?,
                    content_type = ?, content_type_reason = ?,
                    title_cn = ?
                WHERE id = ?
                """,
                (
                    result.score_a, result.score_b, score_c, score_d,
                    result.score_e, result.score_f, total,
                    result.fingerprint, result.veto, json.dumps(result.anxiety_hits, ensure_ascii=False),
                    result.maturity_stage, result.reason,
                    result.content_type, result.content_type_reason,
                    result.title_cn,
                    article.id,
                ),
            )
            scored += 1
            if result.veto:
                veto_count += 1
            type_dist[result.content_type] = type_dist.get(result.content_type, 0) + 1

        conn.commit()

    print(
        f"[score] scored={scored}, errors={errors}, veto={veto_count}, "
        f"content_type 分布={type_dist}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--real-llm", action="store_true",
        help="使用真 LLM（OpenAI 兼容端点，需要 .env 里的 LLM_API_KEY）",
    )
    parser.add_argument("--rescore-all", action="store_true", help="强制重打所有文章")
    parser.add_argument(
        "--concurrency", type=int, default=None,
        help="真 LLM 模式并发数（默认取 .env 里的 LLM_CONCURRENCY，否则 4）",
    )
    args = parser.parse_args()

    if args.real_llm:
        import os as _os
        double_pass = _os.getenv("LLM_DOUBLE_PASS", "1") != "0"
        scorer: ArticleScorer = OpenAICompatScorer(double_pass=double_pass)
        concurrency = args.concurrency or scorer.cfg.concurrency  # type: ignore[attr-defined]
        dp_flag = "双评取高" if double_pass else "单评"
        print(f"[score] 真 LLM 模式 · model={scorer.cfg.model} · {dp_flag} · concurrency={concurrency}")  # type: ignore[attr-defined]
    else:
        scorer = MockScorer()
        concurrency = 1
        print("[score] Mock 模式（启发式，不调用 LLM）")

    run(scorer, rescore_all=args.rescore_all, concurrency=concurrency)
    return 0


if __name__ == "__main__":
    sys.exit(main())
