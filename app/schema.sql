-- 《网络治理动态速递》选题情报库 schema
-- 对应 scoring_spec_v1.md（v1.0 + v1.1 patch）

PRAGMA foreign_keys = ON;

-- ============================================================
-- articles：所有抓回的文章 + 评分结果
-- ============================================================
CREATE TABLE IF NOT EXISTS articles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 来源信息
    url             TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    summary         TEXT,                       -- 摘要/导语（送给 LLM 评分用）
    source_name     TEXT NOT NULL,              -- e.g. "DataGuidance (KtN)"
    source_tier     TEXT NOT NULL CHECK (source_tier IN ('S','A','B','C','D')),
    published_at    TEXT NOT NULL,              -- ISO 8601 UTC
    fetched_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- v1.0 评分维度（0-100）
    score_a         INTEGER,                    -- 网信办关切度 0-30
    score_b         INTEGER,                    -- 议题成熟度 0-20
    score_c         INTEGER,                    -- 信源权威度 0-15
    score_d         INTEGER,                    -- 议题热度 0-15（聚类后回填）
    score_e         INTEGER,                    -- 产业可借鉴性 0-10
    score_f         INTEGER,                    -- 稀缺性 0-10
    total_score     INTEGER,                    -- A+B+C+D+E+F

    -- v1.0 LLM 输出元数据
    fingerprint     TEXT,                       -- 议题指纹，用于聚类
    veto            TEXT,                       -- 命中的一票否决项编号 / NULL
    anxiety_hits    TEXT,                       -- JSON array of 焦虑点名
    maturity_stage  TEXT,                       -- 风险冒头期/讨论立法期/规则成形期/落地执行期
    reason          TEXT,                       -- 一句话理由

    -- v1.1 patch：内容类型二分
    content_type    TEXT NOT NULL DEFAULT 'opinion_analysis'
                    CHECK (content_type IN
                        ('fact_legislative','fact_enforcement','fact_official_doc','opinion_analysis')),
    content_type_reason TEXT,

    -- v1.2 patch：LLM 生成的中文标题（原 title 仍保留）
    title_cn        TEXT
);

CREATE INDEX IF NOT EXISTS idx_articles_published    ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_fingerprint  ON articles(fingerprint);
CREATE INDEX IF NOT EXISTS idx_articles_content_type ON articles(content_type);
CREATE INDEX IF NOT EXISTS idx_articles_total        ON articles(total_score DESC);
-- v1.1 双栏 Top 查询主索引：按"时间窗 + 类型 + 分数"组合命中
-- （time_window 在查询时由 published_at 推导，索引仍按"类型 + 分数 + 发布时间"足够）
CREATE INDEX IF NOT EXISTS idx_articles_type_score_pub
    ON articles(content_type, total_score DESC, published_at DESC);

-- ============================================================
-- daily_picks：每日 4 档时间窗的 Top 选题（含双栏）
-- ============================================================
CREATE TABLE IF NOT EXISTS daily_picks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT NOT NULL,              -- YYYY-MM-DD
    time_window     TEXT NOT NULL CHECK (time_window IN ('24h','72h','120h','360h')),
    column_kind     TEXT NOT NULL CHECK (column_kind IN ('facts','opinions')),
    rank            INTEGER NOT NULL,           -- 1=Top1, 2=Top2, 3=Top3, 4..=情报池
    article_id      INTEGER NOT NULL,
    is_top3         INTEGER NOT NULL CHECK (is_top3 IN (0,1)),

    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE,
    UNIQUE (snapshot_date, time_window, column_kind, rank)
);

-- ============================================================
-- clusters：议题聚类（同 fingerprint 多文章合并视图）
-- ============================================================
CREATE TABLE IF NOT EXISTS clusters (
    fingerprint     TEXT PRIMARY KEY,
    main_article_id INTEGER NOT NULL,           -- 主条（fact 优先，无 fact 才退到 opinion）
    main_is_fact    INTEGER NOT NULL CHECK (main_is_fact IN (0,1)),
    article_count   INTEGER NOT NULL,           -- 同议题文章总数（用于 D 维度）
    last_updated    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (main_article_id) REFERENCES articles(id) ON DELETE CASCADE
);

-- ============================================================
-- fingerprint_history：30 天判重池（已发表速递的议题指纹）
-- ============================================================
CREATE TABLE IF NOT EXISTS fingerprint_history (
    fingerprint     TEXT PRIMARY KEY,
    published_date  TEXT NOT NULL,              -- 哪天的速递写过
    note            TEXT
);

-- ============================================================
-- feed_health：信源健康度（每个 feed 的最新一次抓取结果 + 近 7 天历史）
-- ============================================================
-- 设计：
--   feed_health          存"每个 feed 当前状态"（upsert by source_name）
--   feed_health_log      存"每次 fetch 的成功/失败记录"，用于算近 7 天成功率
-- 这样 about 页一次查询能算出：最后成功时间 / 连续失败天数 / 近 7 天 N/7
CREATE TABLE IF NOT EXISTS feed_health (
    source_name         TEXT PRIMARY KEY,
    feed_url            TEXT NOT NULL,
    source_tier         TEXT,
    last_attempt_at     TEXT NOT NULL,      -- ISO 8601 UTC：最近一次 fetch 时刻（不论成败）
    last_success_at     TEXT,               -- ISO 8601 UTC：最近一次成功时刻，可为 NULL
    last_error          TEXT,               -- 最近一次失败的错误描述；成功时清空
    last_article_count  INTEGER DEFAULT 0,  -- 最近一次成功时该 feed 里的文章条数
    consecutive_fails   INTEGER DEFAULT 0   -- 连续失败次数；一次成功即归 0
);

CREATE TABLE IF NOT EXISTS feed_health_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name         TEXT NOT NULL,
    attempted_at        TEXT NOT NULL,      -- ISO 8601 UTC
    success             INTEGER NOT NULL CHECK (success IN (0,1)),
    article_count       INTEGER DEFAULT 0,
    error               TEXT
);

CREATE INDEX IF NOT EXISTS idx_feed_log_source_time
    ON feed_health_log(source_name, attempted_at DESC);

-- ============================================================
-- feedback：人工 👍/👎 反馈（MVP 只记录不自动调权重）
-- ============================================================
CREATE TABLE IF NOT EXISTS feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id      INTEGER NOT NULL,
    fingerprint     TEXT,
    vote            INTEGER NOT NULL CHECK (vote IN (-1, 1)),  -- -1=👎, 1=👍
    user_note       TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
);
