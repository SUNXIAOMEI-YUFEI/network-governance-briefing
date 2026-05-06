# 网络治理动态速递 · 选题情报工作台

> 给互联网大厂法律研究团队的内部工具：每天打开一个网页，看到已评分的域外网络治理选题（Top 3 + 情报池），辅助起草《网络治理动态速递》。

## 当前状态

**Step 2 进行中**：Mock 数据跑通流水线（无需 Inoreader / Anthropic API key）

```
fetch_mock.py → score.py（mock 桩） → cluster.py → build_today.py → data/today.json
```

跑通后再接真 API（Step 3-4）。

## 快速开始

```bash
# 1. 装依赖（最小化，目前只需 stdlib + 一两个）
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 初始化数据库
python3 -m app.init_db

# 3. 灌 mock 数据
python3 -m app.fetch_mock

# 4. 评分（mock 桩，不调真 LLM）
python3 -m app.score

# 5. 聚类 + 出今日 JSON
python3 -m app.build_today

# 6. 查看产物
cat data/today.json | python3 -m json.tool | head -80
```

## 目录结构

```
app/
  __init__.py
  init_db.py        # 建表（articles / clusters / daily_picks / feedback）
  fetch_mock.py     # 写 mock 文章入库（无网络）
  fetch.py          # 真 Inoreader 拉取（Step 4）
  score.py          # 评分（含 mock 桩 + 真 Claude 调用 两种模式）
  cluster.py        # 议题聚类，fact 提主 opinion 折叠
  build_today.py    # 出 data/today.json
  config.py         # 信源映射 / 黑名单 / 关键词
  schema.sql        # SQLite 建表 SQL
  prompts/
    score_article.md  # LLM 评分 prompt（v1.1，含 content_type 判定）
  data/
    mock_articles.json     # mock 数据集（12-16 条）
    industry_blacklist.txt # 产业政策黑名单
data/
  briefing.db       # SQLite（运行时生成）
  today.json        # 今日产物（运行时生成）
  archive/          # 历史快照
requirements.txt
README.md
```

## 关键设计文档（在 brain 目录）

- `scoring_spec_v1.md` —— 评分标准 v1.0 + v1.1 patch（必读）
- `research_cac_profile.md` —— 网信办 8 大焦虑点
- `research_intel_sources.md` —— 38 个 RSS 信源
- `source_credentials.md` —— DataGuidance 等付费源接入凭证

## v1.1 关键点（content_type 二分）

每篇文章打 4 类标签之一：
- `fact_legislative` 立法事实 / `fact_enforcement` 执法事实 / `fact_official_doc` 官方文件 / `opinion_analysis` 观点分析

输出端**左右双栏 + 双 Top 3**：左边 fact_*（写速递的"骨"）、右边 opinion（写速递的"肉"），名额完全独立、互不挤占。

## ⚠️ Mock 桩的已知误差（Step 4 接真 LLM 后失效）

`MockScorer` 用关键词启发式做 content_type 判定，存在 ~15% 误判率，已知模式：

- **Op-ed 文章引用了"X 国发布报告"** → 命中 fact_official_doc 词，被误判为 fact（如 Tech Policy Press "The Myth of..."）
- **比较分析类长文含 "consultation papers"** → 同上（如 IAPP "Comparative Analysis"）
- 真 LLM 阶段这些都不是问题——Claude 看到 "Myth of European AI Leadership" 一眼就归 opinion

**Step 4 接真 LLM 时要做的清理**：

- 删除 `app/score.py` 中 `MockScorer._detect_content_type` 内的"律所/监管者兜底"代码块（真 LLM 不需要）
- 删除 `app/config.py` 中的 `CONTENT_TYPE_SIGNALS`（真 LLM 不需要这套关键词）
- 用 `prompts/score_article.md` 的 prompt 直接调 Claude，把 LLM 返回的 `content_type` 字段写进 DB 即可
