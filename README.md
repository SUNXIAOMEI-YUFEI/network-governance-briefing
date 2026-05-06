# 选题情报工作台

> 法律研究团队的内部工具：每天打开一个网页，看到已评分的域外网络治理选题（Top 3 + 情报池），辅助起草内部情报简报。

## 当前状态

**v1.2 已上线**：真数据流水线每天自动跑（GitHub Actions · DeepSeek V3 评分 · Vercel 托管）

```
fetch.py → score.py → cluster.py → build_today.py → data/today.json → Vercel
```

## 快速开始

```bash
# 1. 装依赖（最小化，目前只需 stdlib）
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # 目前为空，留给未来扩展

# 2. 初始化数据库
python3 -m app.init_db

# 3. 抓取真 RSS（过去 24 小时）
python3 -m app.fetch --hours 24

# 4. 评分（需 .env 配好 LLM_API_KEY）
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
  init_db.py        # 建表（articles / clusters / daily_picks / feedback / feed_health）
  fetch.py          # 真 RSS 拉取（并发、去重、健康度记录）
  fetch_mock.py     # 本地冒烟测试用的 mock 数据
  score.py          # LLM 评分（OpenAI 兼容协议，DeepSeek/Claude 可切换）
  cluster.py        # 议题聚类，fact 提主 opinion 折叠
  build_today.py    # 出 data/today.json（v1.2 增加 feed_health 字段）
  config.py         # 信源映射 / 黑名单 / 关键词
  schema.sql        # SQLite 建表 SQL
  llm_client.py     # 纯 stdlib 的 OpenAI 兼容 HTTP 客户端
  prompts/
    score_article.md  # LLM 评分 prompt（v1.2）
  data/
    mock_articles.json     # mock 数据集
    industry_blacklist.txt # 产业政策黑名单
scripts/
  ci_run.py         # CI 入口：init_db + fetch + score + cluster + build
  purge_mock.py     # 清理 mock 数据
data/
  briefing.db       # SQLite（运行时生成）
  today.json        # 今日产物（运行时生成）
  archive/          # 历史快照
v2/
  index.html        # 主界面（双栏 Top 3 + 议题聚类 + 情报池）
  app.js            # 渲染逻辑 + 收藏夹
  style.css         # 主样式
  favorites.html/js # 我的收藏（localStorage）
  about.html        # 关于本站 + 信源健康度
  pages.css         # 收藏夹 + 关于页样式
.github/workflows/
  daily.yml         # 每日流水线（北京 14:00 自动跑）
```

## v1.2 关键特性

- **内容类型二分**：每篇文章打 `fact_legislative` / `fact_enforcement` / `fact_official_doc` / `opinion_analysis` 之一
- **左右双栏 + 双 Top 3**：左事实（简报的"骨"）、右观点（简报的"肉"），名额完全独立
- **4 档时间窗**：过去 24h / 72h / 5 天 / 15 天各自计算 Top
- **中文标题归纳**：LLM 为每篇英文文章输出 15-30 字的简洁中文标题（`title_cn`）
- **议题聚类**：同议题被多个信源报道时，按 `fingerprint` 合并并排序
- **收藏夹**：👍 即存，localStorage 方案，支持 JSON 导出/导入跨设备同步
- **信源健康度**：about 页展示每个 feed 的最后成功时间、近 7 天成功率、错误详情
- **脱敏文案**：对外展示层通过 `sanitize()` 过滤历史 LLM 输出中的内部口径词

## 关键设计文档

- `scoring_spec_v1.md` —— 评分标准完整规范（本地）
- `research_intel_sources.md` —— 信源金字塔与订阅策略（本地）
- `DEPLOY.md` —— GitHub Actions + Vercel 部署指南

## 在线访问

Vercel 部署：https://network-governance-briefing.vercel.app/v2/index.html

每天北京时间 14:00-14:30 自动刷新一次。
