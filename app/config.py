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
    ("Don't Worry About the Vase", "https://thezvi.substack.com/feed",                    "D"),
    ("AI Snake Oil",               "https://arvindnarayanan.substack.com/feed",           "D"),
    ("Platformer",                 "https://www.platformer.news/feed",                    "C"),

    # === 1. 政治内容 ===
    ("Tech Policy Press",          "https://www.techpolicy.press/feed",                   "A"),
    ("Lawfare",                    "https://www.lawfaremedia.org/feeds/cybersecurity-tech.xml", "A"),
    ("The Register",               "https://www.theregister.com/headlines.atom",          "C"),

    # === 2. 算法极化 ===
    ("AlgorithmWatch",             "https://algorithmwatch.org/en/feed",                  "C"),
    ("AI Now",                     "https://ainowinstitute.org/feed",                     "C"),
    ("Brookings TechTank",         "https://www.brookings.edu/blog/techtank/feed",        "B"),

    # === 3. 未成年人保护 ===
    ("Future of Privacy Forum",    "https://fpf.org/feed",                                "A"),
    ("Ada Lovelace",               "https://www.adalovelaceinstitute.org/feed",           "C"),

    # === 4. 训练数据 / 跨境 ===
    ("DLA Piper",                  "https://privacymatters.dlapiper.com/feed",            "A"),
    ("Inside Privacy (Covington)", "https://www.insideprivacy.com/feed",                  "A"),
    ("EPIC",                       "https://epic.org/feed",                               "C"),
    ("CDT",                        "https://cdt.org/feed",                                "C"),

    # === 5. 内容标识 ===
    ("NIST",                       "https://www.nist.gov/news-events/news/rss.xml",       "S"),

    # === 6. Agent / 新形态 ===
    ("OII",                        "https://www.oii.ox.ac.uk/news-events/news/feed",      "C"),

    # === 7. 平台垄断 ===
    ("CMA UK",                     "https://www.gov.uk/government/organisations/competition-and-markets-authority.atom", "S"),
    ("FTC",                        "https://www.ftc.gov/feeds/press-release.xml",         "S"),
    ("Politico EU Tech",           "https://www.politico.eu/section/technology/feed",     "B"),

    # === 9. UK / 多辖区 ===
    ("ICO",                        "https://ico.org.uk/about-the-ico/media-centre/news/news.rss", "S"),
    ("Ofcom",                      "https://www.ofcom.org.uk/news-centre/feed",           "S"),
    ("OAIC",                       "https://www.oaic.gov.au/news.atom",                   "S"),

    # === A. 通用科技（关键词过滤） ===
    ("TechCrunch",                 "https://techcrunch.com/feed/",                        "C"),

    # === KtN 转 RSS（邮件订阅）===
    # 注意：以下条目和上面同名时，URL 不同；按 URL 去重，两路都跑确保覆盖
    ("DataGuidance (KtN)",         "https://kill-the-newsletter.com/feeds/0t72e882tjox9e1mztov.xml", "A"),
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
