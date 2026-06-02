"""配置：信源映射、产业政策黑名单、路径常量。

不依赖任何外部库，纯 stdlib。
"""
from pathlib import Path

# ---------- 路径 ----------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR      = PROJECT_ROOT / "app"
DATA_DIR     = PROJECT_ROOT / "data"
ARCHIVE_DIR  = DATA_DIR / "archive"
DB_PATH      = DATA_DIR / "briefing.db"
TODAY_JSON   = DATA_DIR / "today.json"
SCHEMA_PATH  = APP_DIR / "schema.sql"
MOCK_PATH    = APP_DIR / "data" / "mock_articles.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


# ---------- 信源 → 档次映射（C 维度，scoring_spec_v1.md §1.3） ----------
# S(15) / A(12) / B(8) / C(5) / D(2)
SOURCE_AUTHORITY: dict[str, str] = {
    # --- S 级官方 ---
    "European Commission":          "S",
    "Ofcom":                        "S",
    "FTC":                          "S",
    "NIST":                         "S",
    "NIST AISI (KtN)":              "S",
    "ICO":                          "S",
    "CNIL":                         "S",
    "CNIL (KtN)":                   "S",
    "Garante":                      "S",
    "EDPB":                         "S",
    "EDPS (KtN)":                   "S",
    "White House":                  "S",
    "DOJ":                          "S",
    "CMA UK":                       "S",
    "OAIC":                         "S",
    "OAIC (KtN)":                   "S",

    # --- A 级 顶律所/顶刊 ---
    "DataGuidance (KtN)":           "A",
    "IAPP Daily Dashboard (KtN)":   "A",
    "Inside Privacy (Covington)":   "A",
    "Inside Privacy (KtN)":         "A",
    "WilmerHale Privacy":           "A",
    "WilmerHale Privacy (KtN)":     "A",
    "Hogan Lovells Engage":         "A",
    "Tech Policy Press":            "A",
    "TechPolicy Press (KtN)":       "A",
    "Lawfare":                      "A",
    "Lawfare (KtN)":                "A",
    "Future of Privacy Forum":      "A",
    "DLA Piper":                    "A",
    "HAI Stanford (KtN)":           "A",

    # --- B 级 主流媒体 ---
    "Politico Pro":                 "B",
    "Politico EU Tech":             "B",
    "MLex":                         "B",
    "MLex AI (KtN)":                "B",
    "Reuters Tech":                 "B",
    "FT Tech":                      "B",
    "NYT Tech":                     "B",
    "Bloomberg Tech":               "B",
    "Brookings TechTank":           "B",
    "Brookings TechTank (KtN)":     "B",

    # --- C 级 行业媒体 ---
    "TechCrunch":                   "C",
    "The Verge":                    "C",
    "The Register":                 "C",
    "Wired":                        "C",
    "IAPP":                         "C",
    "The Record":                   "C",
    "AlgorithmWatch":               "C",
    "AI Now":                       "C",
    "Ada Lovelace":                 "C",
    "OII":                          "C",
    "EPIC":                         "C",
    "CDT":                          "C",
    "Platformer":                   "C",

    # --- D 级 社媒 ---
    "Import AI (Substack)":         "D",
    "Don't Worry About the Vase":   "D",
    "AI Snake Oil":                 "D",
}

TIER_TO_SCORE = {"S": 15, "A": 12, "B": 8, "C": 5, "D": 2}


# ---------- 8 大焦虑点关键词（A 维度，scoring_spec_v1.md §1.1） ----------
ANXIETY_KEYWORDS: dict[str, list[str]] = {
    "政治内容失控":   ["deepfake", "election interference", "disinfo", "foreign influence",
                       "深度伪造", "选举干预", "虚假信息"],
    "算法极化":       ["algorithmic amplification", "filter bubble", "recommender harm",
                       "algorithm transparency", "算法推荐", "信息茧房"],
    "未成年人极端事件": ["minor self-harm", "AI companion suicide", "child safety",
                       "age assurance", "未成年人", "self-harm"],
    "训练数据跨境":   ["training data transfer", "cross-border data", "model training data",
                       "data localization", "数据出境"],
    "Agent 等新形态": ["AI agent governance", "autonomous agent", "embodied AI",
                       "agentic AI", "Agent 监管"],
    "平台垄断":       ["DMA enforcement", "gatekeeper", "platform abuse",
                       "antitrust", "平台垄断", "数字市场法"],
    "标识失效":       ["watermark", "content provenance", "AI labeling",
                       "C2PA", "标识办法", "synthetic media labeling"],
    "涉企黑嘴":       ["China tech criticism", "Chinese platform abuse",
                       "抹黑", "中国科技公司"],
}

ANXIETY_HIGH_WEIGHT = {"政治内容失控", "算法极化", "未成年人极端事件",
                       "训练数据跨境", "Agent 等新形态", "平台垄断"}
ANXIETY_MID_WEIGHT  = {"标识失效", "涉企黑嘴"}


# ---------- 产业政策黑名单（一票否决 #6） ----------
INDUSTRY_BLACKLIST: list[str] = [
    "semiconductor", "chip ban", "GPU export", "ASML",
    "Huawei sanction", "entity list", "BIS export control",
    "tariff", "trade war", "supply chain decoupling",
    "半导体", "芯片管制", "出口管制", "实体清单", "关税战",
]


# ---------- 国内网信办执法关键词黑名单（一票否决 #5） ----------
DOMESTIC_CAC_BLACKLIST: list[str] = [
    "网信办处罚", "网信办通报", "中国网信办执法",
    "Cyberspace Administration of China enforcement",
    "CAC fines", "CAC penalty",
]


# ---------- 议题成熟度关键词（B 维度，scoring_spec_v1.md §1.2） ----------
MATURITY_SIGNALS = {
    "讨论立法期": ["draft", "proposed", "consultation", "RFI",
                   "request for information", "征求意见", "hearing", "inquiry",
                   "white paper", "green paper"],
    "规则成形期": ["adopted", "passed", "final rule", "已颁布", "已通过"],
    "落地执行期": ["enforced", "in effect", "fine", "penalty", "lawsuit",
                   "已执法", "处罚", "sanction imposed"],
    # 风险冒头期 = 兜底（无以上信号 + 学界/媒体首次报道）
}


# ---------- v1.1 content_type 关键词信号（启发式辅助 LLM） ----------
# 注意：真 LLM 评分时这些只是 prompt 里的 hint；mock 桩评分时是主要依据
CONTENT_TYPE_SIGNALS = {
    "fact_legislative": [
        "signed", "enacted", "effective", "takes effect", "comes into force",
        "passed", "introduces a bill", "amendment", "regulation entered",
        "finalizes", "finalises", "final rule", "rule update", "into law",
        "签署", "生效", "颁布", "通过", "修订",
    ],
    "fact_enforcement": [
        "fines", "fined", "penalty", "settles", "settlement",
        "court rules", "ruling", "judgment", "investigation launched",
        "launches investigation", "launches", "opens probe", "second wave of",
        "wave of enforcement", "enforcement action",
        "处罚", "判决", "和解", "立案",
    ],
    "fact_official_doc": [
        "publishes", "issues guidance", "releases report", "releases",
        "consultation paper", "request for information", "RFI",
        "policy statement", "profile", "framework",
        "发布", "印发", "出台指南", "咨询文件",
    ],
    "opinion_analysis": [
        "why", "how", "should", "argues", "analysis", "explainer",
        "op-ed", "commentary", "perspective", "我们认为",
        "三个理由", "the case for", "the case against",
        "myth of", "won't solve", "is failing", "the coming",
    ],
}


# ============================================================
# RSS 信源清单（Step 4 真数据接入）
# ============================================================
#
# 来源：phase1_step2_opml_v2.opml（25 条主源）+ phase1_opml_v2_ktn_addon.opml（11 条 KtN 转 RSS）
# 加上 source_credentials.md §1 的 DataGuidance KtN feed，共 ~37 个。
#
# 每条 (name, url, tier) ：
#   - name 必须和 SOURCE_AUTHORITY 里的 key 对得上（用于 C 维度评分）
#   - tier 只是冗余备份，name 找不到 SOURCE_AUTHORITY 时用
#
# 去重策略：URL 字符串去重（同一篇文章两个源都给了，保留先到的）。

RSS_FEEDS: list[tuple[str, str, str]] = [
    # === 0. 高信号必扫源 ===
    ("Import AI (Substack)",       "https://jack-clark.net/feed",                         "D"),
    # 已删 (2026-06-02)：Substack 反爬升级，连 30 天 HTTP 403。
    #   - "Don't Worry About the Vase"  https://thezvi.substack.com/feed
    #   - "AI Snake Oil"                https://arvindnarayanan.substack.com/feed
    # 如要恢复：通过 KtN 邮件订阅（订阅 newsletter → KtN 转 RSS）。
    ("Platformer",                 "https://www.platformer.news/feed",                    "C"),

    # === 1. 政治内容 ===
    ("Tech Policy Press",          "https://www.techpolicy.press/feed",                   "A"),
    # 已删 Lawfare 主源（HTTP 403），改用 "Lawfare (KtN)" 邮件中转（见下方 KtN 区）
    # The Register 已移到下方"通用科技 (FEED_PREFILTER)"区，参与关键词预筛 + 8 条限流

    # === 2. 算法极化 ===
    # 已删 AlgorithmWatch（feed XML parse error 长期失败），无 KtN 备份；放弃该源
    ("AI Now",                     "https://ainowinstitute.org/feed",                     "C"),
    # 已删 Brookings TechTank 主源（XML parse error），改用 "Brookings TechTank (KtN)" 邮件中转

    # === 3. 未成年人保护 ===
    ("Future of Privacy Forum",    "https://fpf.org/feed",                                "A"),
    ("Ada Lovelace",               "https://www.adalovelaceinstitute.org/feed",           "C"),

    # === 4. 训练数据 / 跨境 ===
    ("DLA Piper",                  "https://privacymatters.dlapiper.com/feed",            "A"),
    ("Inside Privacy (Covington)", "https://www.insideprivacy.com/feed",                  "A"),
    ("EPIC",                       "https://epic.org/feed",                               "C"),
    # 已删 CDT（HTTP 403 反爬，无 KtN 替代）

    # === 5. 内容标识 ===
    ("NIST",                       "https://www.nist.gov/news-events/news/rss.xml",       "S"),

    # === 6. Agent / 新形态 ===
    ("OII",                        "https://www.oii.ox.ac.uk/news-events/news/feed",      "C"),

    # === 7. 平台垄断 ===
    ("CMA UK",                     "https://www.gov.uk/government/organisations/competition-and-markets-authority.atom", "S"),
    ("FTC",                        "https://www.ftc.gov/feeds/press-release.xml",         "S"),
    ("Politico EU Tech",           "https://www.politico.eu/section/technology/feed",     "B"),

    # === 9. UK / 多辖区 ===
    # 已删 ICO（2026 年改版彻底没了 RSS feed，全 404）
    # 已删 Ofcom（Cloudflare 反爬，所有 URL 候选均返回 "Just a moment..." 403）
    # 已删 OAIC 主源（HTTP 404），改用 "OAIC (KtN)" 邮件中转
    # 待补：未来通过 KtN 邮件订阅 Ofcom/ICO 官方 newsletter 后，加 KtN 中转 feed

    # === A. 通用科技（关键词预筛 + 限流，见 fetch.py / FEED_PREFILTER）===
    ("TechCrunch",                 "https://techcrunch.com/feed/",                        "C"),
    # 2026-06-02 重新加回：之前删是因为八卦多，但用户复审后认为 The Register
    # 有不少观点类（opinion_analysis）内容值得保留。配套保护：
    #   1. 已在 FEED_PREFILTER_SOURCES，关键词预筛把"网红八卦"挡在入口
    #   2. FEED_MAX_ITEMS["The Register"] = 8，单次最多 8 条
    #   3. SOURCE_AUTHORITY 已为 "C" 级
    ("The Register",               "https://www.theregister.com/headlines.atom",          "C"),

    # === KtN 转 RSS（邮件订阅）===
    # 注意：以下条目和上面同名时，URL 不同；按 URL 去重，两路都跑确保覆盖
    ("DataGuidance (KtN)",         "https://kill-the-newsletter.com/feeds/0t72e882tjox9e1mztov.xml", "A"),
    ("IAPP Daily Dashboard (KtN)", "https://kill-the-newsletter.com/feeds/9v0c5wjcqvoklno87tca.xml", "A"),
    ("Lawfare (KtN)",              "https://kill-the-newsletter.com/feeds/eqkwbtfnwj1lqbi08d5p.xml", "A"),
    ("Inside Privacy (KtN)",       "https://kill-the-newsletter.com/feeds/eyvqcb5thrgv7pt4umj2.xml", "A"),
    ("MLex AI (KtN)",              "https://kill-the-newsletter.com/feeds/0ir80wigiatnud8bexc0.xml", "B"),
    ("WilmerHale Privacy (KtN)",   "https://kill-the-newsletter.com/feeds/muwfbzqx60g7b14mspkf.xml", "A"),
    ("HAI Stanford (KtN)",         "https://kill-the-newsletter.com/feeds/n4hhyacgt1an1008eea6.xml", "A"),
    ("TechPolicy Press (KtN)",     "https://kill-the-newsletter.com/feeds/3yjfc4v17nj9t1yrkhnx.xml", "A"),
    ("Brookings TechTank (KtN)",   "https://kill-the-newsletter.com/feeds/btrel8v1yu4h5i4xcsze.xml", "B"),
    ("EDPS (KtN)",                 "https://kill-the-newsletter.com/feeds/57vrbnl13yfzqwv3teb0.xml", "S"),
    ("CNIL (KtN)",                 "https://kill-the-newsletter.com/feeds/jh7gg5otvnfka6k2pfcp.xml", "S"),
    ("OAIC (KtN)",                 "https://kill-the-newsletter.com/feeds/4n2ehrjdddcz86rcari4.xml", "S"),
    ("NIST AISI (KtN)",            "https://kill-the-newsletter.com/feeds/h7ma698632mz98ydqg93.xml", "S"),
]


# ============================================================
# 信源限流 + 关键词预筛（v1.7，2026-06-02）
# ============================================================
#
# 背景：5/30 起 24h tab 全空——根因是 TechCrunch / The Register 等通用科技站
# 每天发 30-80 条新闻，其中 95% 与"五大治理主题"无关（YouTuber 拍电影、
# 火箭展览、SoftBank 投资数据中心等），全被 LLM 判 veto=7，但已经吃光了
# LLM 评分配额，挤掉真正高质量信源（律所 / 智库 / 监管官方）的份额。
#
# 修复策略：在 fetch 阶段（不调 LLM）做廉价的关键词预筛：
#   - 标题或摘要含治理关键词 → 进库（让 LLM 评分）
#   - 全没命中 → 直接丢弃，不入库不调 LLM
#
# 这样 TechCrunch / The Register 真正相关的"FTC 起诉 Meta"还能进来，
# 但"网红拍电影"这种就被廉价的 grep 在入口拦掉了。

# 信源单次抓取上限：超出按 RSS 倒序裁剪。None / 不在表里 = 不限
FEED_MAX_ITEMS: dict[str, int] = {
    "TechCrunch":   12,   # 默认 RSS 通常 20 条，留治理类
    "The Register": 8,    # 八卦多，紧一点
    "The Verge":    12,
    "Wired":        12,
    "Reuters Tech": 15,
    "FT Tech":      15,
    "NYT Tech":     15,
    "Bloomberg Tech": 15,
}

# 启用关键词预筛的信源（C/D 级通用科技/媒体类）
# 律所、智库、官方源不预筛——它们本身就专业，发的就是治理内容
FEED_PREFILTER_SOURCES: set[str] = {
    "TechCrunch",
    "The Register",
    "The Verge",
    "Wired",
    "Reuters Tech",
    "FT Tech",
    "NYT Tech",
    "Bloomberg Tech",
    "The Record",
    "Platformer",
}

# 治理关键词（标题 + 摘要任一命中即放行；大小写不敏感）
# 覆盖五大主题（焦虑点）+ 监管者/法规/裁判术语
GOVERNANCE_KEYWORDS: list[str] = [
    # 法规与立法
    "regulation", "regulatory", "regulator", "regulate",
    "law", "lawsuit", "legislation", "bill", "act", "statute",
    "compliance", "enforcement", "enforce", "fine", "fined", "penalty",
    "settlement", "settle", "investigation", "probe", "subpoena",
    "ruling", "ruled", "court", "judge", "judgment", "verdict",
    "appeal", "appeal court", "supreme court",
    # 监管机构（精确名）
    "ftc", "fcc", "doj ", " doj", "sec filing", "sec rule", "sec lawsuit",
    "ofcom", "ico ", "cnil", "edpb", "edps",
    "garante", "cma", "european commission", "white house",
    "ec ", "dsa", "dma", "ai act", "gdpr", "ccpa", "cpra", "coppa",
    "nist", "nis2", "cyber resilience act",
    # 五大主题/八大焦虑（对应 ANXIETY_KEYWORDS 的英文）
    "deepfake", "election", "disinfo", "misinformation", "foreign influence",
    "algorithm", "recommender", "filter bubble",
    "minor", "child safety", "self-harm", "age assurance", "age verification",
    "cross-border data", "data transfer", "data localization", "training data",
    "ai agent", "agentic", "autonomous agent", "embodied ai",
    "antitrust", "monopoly", "gatekeeper", "platform abuse",
    "watermark", "provenance", "c2pa", "synthetic media", "labeling",
    # 隐私 / 安全 / 治理通用
    "privacy", "data protection", "personal data", "biometric",
    "ai governance", "ai safety", "ai policy", "ai ethics",
    "content moderation", "child safety", "transparency report",
    "section 230", "intermediary liability",
    # 中文（防漏）
    "监管", "执法", "处罚", "起诉", "判决", "立法", "草案", "法案",
    "数据保护", "隐私", "未成年人", "深度伪造", "算法", "标识",
]


# ---------- KtN newsletter 噪声过滤（事务邮件，不进评分） ----------
# 当 source_name 含 "(KtN)" 时启用；标题里命中下列任一关键词即丢弃
KTN_NOISE_PATTERNS: list[str] = [
    "welcome to", "welcome!",
    "activate your", "activation required",
    "verify your email", "please confirm",
    "your trial", "trial expires", "trial expir",
    "renew your", "subscription has been renewed",
    "your password", "password reset",
    "unsubscribe", "you are subscribed",
    "kill the newsletter",
    "test message",
]


# ---------- HTTP 抓取参数 ----------
HTTP_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 "
    "(briefing-fetcher; legal-research)"
)
HTTP_TIMEOUT_SEC  = 25
FETCH_CONCURRENCY = 8     # 并发抓 feed 的线程数
