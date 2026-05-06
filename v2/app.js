/* 选题情报工作台 · v1.2
 * 读 ../data/today.json 渲染：双栏 Top 3 + 议题聚类 + 情报池 + 反馈按钮
 */

// ---------- 配置 ----------
// 支持本地（相对路径）和 Vercel（根路径）两种部署
const TODAY_JSON_CANDIDATES = [
  "/data/today.json",       // Vercel / 部署版
  "../data/today.json",     // 本地 http.server 8765
  "./data/today.json",      // 兜底
];
const FEEDBACK_KEY   = "briefing.feedback.v1";
const FAV_KEY        = "briefing.favorites.v1";  // v1.2：收藏夹（点赞 = 加收藏）

// ---------- 对外文案脱敏 ----------
// 目的：历史 LLM 输出（reason / anxiety_hits）里含有只用于内部口径的词
// （例如"网信办"、"8 大焦虑点"、"涉企黑嘴"等）。前端展示前统一替换为中性词，
// 底层数据不动，不影响评分逻辑。未来 prompt 升级后新数据自然就是中性文案。
const SANITIZE_MAP = [
  // 先长后短，避免"网信办"替完后 "网信办关切" 再二次替换
  [/中央网信办关切/g,   "政策关切议题"],
  [/网信办关切/g,       "政策关切议题"],
  [/网信办核心关切/g,   "核心关切议题"],
  [/网信办自身执法/g,   "国内主管部门自身执法"],
  [/网信办/g,           "监管者"],
  [/8\s*大焦虑点/g,     "关切议题清单"],
  [/八大焦虑点/g,       "关切议题清单"],
  [/高焦虑点/g,         "高关切议题"],
  [/焦虑点/g,           "关切议题"],
  [/涉企黑嘴/g,         "涉企舆论风险"],
  [/《网络治理动态速递》/g, "内部情报简报"],
  [/网络治理动态速递/g,  "内部情报简报"],
  [/写速递/g,           "写简报"],
  [/速递/g,             "简报"],
];

function sanitize(text) {
  if (!text) return text;
  let s = String(text);
  for (const [re, rep] of SANITIZE_MAP) s = s.replace(re, rep);
  return s;
}

// content_type → 中文 + 徽标
const TYPE_LABEL = {
  fact_legislative:  { icon: "📋", text: "立法事实" },
  fact_enforcement:  { icon: "⚖️", text: "执法事实" },
  fact_official_doc: { icon: "📄", text: "官方文件" },
  opinion_analysis:  { icon: "💭", text: "观点分析" },
};
const FACT_TYPES = new Set(["fact_legislative", "fact_enforcement", "fact_official_doc"]);

// ---------- 工具 ----------
const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function fmtDate(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", { hour12: false, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  } catch (e) { return iso; }
}

function fmtPubDate(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
  } catch (e) { return ""; }
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

// ---------- 反馈（localStorage） ----------
function loadFeedback() {
  try { return JSON.parse(localStorage.getItem(FEEDBACK_KEY) || "{}"); }
  catch { return {}; }
}
function saveFeedback(map) {
  localStorage.setItem(FEEDBACK_KEY, JSON.stringify(map));
}
function setVote(articleId, vote) {
  const map = loadFeedback();
  if (map[articleId] === vote) {
    delete map[articleId];   // 再点一次取消
  } else {
    map[articleId] = vote;
  }
  saveFeedback(map);
  return map[articleId] || null;
}

// ---------- 收藏夹（v1.2：👍 = 加收藏夹） ----------
function loadFavs() {
  try { return JSON.parse(localStorage.getItem(FAV_KEY) || "[]"); }
  catch { return []; }
}
function saveFavs(arr) {
  localStorage.setItem(FAV_KEY, JSON.stringify(arr));
  updateFavBadge();
}
/** 加入收藏（如果已在就不重复） */
function addToFav(article) {
  const cur = loadFavs();
  if (cur.some(f => f.id === article.id)) return;
  // 把整条文章内容做快照存下来（原始内容可能过期/删除，要永久留底）
  const snapshot = {
    ...article,
    savedAt: new Date().toISOString(),
  };
  saveFavs([snapshot, ...cur]);
}
/** 从收藏移除 */
function removeFromFav(articleId) {
  saveFavs(loadFavs().filter(f => f.id !== articleId));
}
/** 更新顶栏收藏数徽章 */
function updateFavBadge() {
  const badge = document.getElementById("fav-count");
  if (badge) {
    const n = loadFavs().length;
    badge.textContent = String(n);
    badge.classList.toggle("zero", n === 0);
  }
}

// ---------- 卡片渲染 ----------
function renderCard(article, { compact = false } = {}) {
  const typeMeta = TYPE_LABEL[article.content_type] || { icon: "•", text: article.content_type };

  const score = el("div", { class: "card-score" },
    el("span", { class: "num" }, String(article.total_score ?? "—")),
    el("span", { class: "label" }, "TOTAL")
  );

  // 中文归纳标题 + 英文原题
  const titleCn = (article.title_cn && article.title_cn.trim()) || article.title || "(无标题)";
  const titleEn = article.title || "";
  const showEnglishLine = titleEn && titleEn !== titleCn;

  const isKtnEntry = (article.url || "").includes("kill-the-newsletter.com/feeds/");
  const ktnHint = isKtnEntry
    ? el("span", { class: "ktn-hint", title: "邮件源：直链是邮件页面，内容在下方摘要" }, "📧 邮件源")
    : null;

  const titleBlock = el("div", { class: "card-title-block" },
    el("a", {
      class: "card-title-cn",
      href: article.url,
      target: "_blank",
      rel: "noopener noreferrer",
      title: titleEn || titleCn,
    }, titleCn),
    showEnglishLine
      ? el("div", { class: "card-title-en" }, titleEn)
      : null,
    ktnHint,
  );

  // badges
  const badges = el("div", { class: "card-badges" });
  badges.appendChild(el("span", {
    class: `badge badge-type-${article.content_type}`,
    title: article.content_type_reason || ""
  }, `${typeMeta.icon} ${typeMeta.text}`));

  badges.appendChild(el("span", { class: `badge badge-tier-${article.source_tier}` },
    article.source_tier + " 级"));

  if (article.maturity_stage) {
    badges.appendChild(el("span", { class: "badge badge-stage" }, article.maturity_stage));
  }
  for (const ax of (article.anxiety_hits || []).slice(0, 2)) {
    badges.appendChild(el("span", { class: "badge badge-anxiety" }, sanitize(ax)));
  }

  // reason（对外展示脱敏）
  const reason = el("div", { class: "card-reason" }, sanitize(article.reason || ""));

  // meta
  const meta = el("div", { class: "card-meta" },
    el("span", {}, article.source_name),
    el("span", { class: "muted" }, "·"),
    el("span", {}, fmtPubDate(article.published_at)),
    el("span", { class: "muted" }, "·"),
    el("a", { href: article.url, target: "_blank", rel: "noopener noreferrer" }, "原文 ↗"),
  );

  // 反馈 + 收藏
  const fbMap = loadFeedback();
  const cur = fbMap[article.id];
  const upBtn = el("button", {
    class: `fb-btn up${cur === 1 ? " active" : ""}`,
    title: "认可这个选题（加入我的收藏）",
    "aria-label": "👍",
  }, "👍");
  const downBtn = el("button", {
    class: `fb-btn down${cur === -1 ? " active" : ""}`,
    title: "选题质量差（记下来改进评分）",
    "aria-label": "👎",
  }, "👎");
  upBtn.addEventListener("click", () => {
    const v = setVote(article.id, 1);
    upBtn.classList.toggle("active", v === 1);
    downBtn.classList.toggle("active", v === -1);
    // v1.2: 点 👍 → 加收藏；再点取消 → 从收藏移除
    if (v === 1) {
      addToFav(article);
    } else {
      removeFromFav(article.id);
    }
  });
  downBtn.addEventListener("click", () => {
    const v = setVote(article.id, -1);
    upBtn.classList.toggle("active", v === 1);
    downBtn.classList.toggle("active", v === -1);
    // 点 👎 时如果之前在收藏夹，也一并移除
    removeFromFav(article.id);
  });
  const feedback = el("div", { class: "feedback" }, upBtn, downBtn);

  return el("article", { class: `card${compact ? " compact" : ""}` },
    score, titleBlock, badges, reason, meta, feedback
  );
}

function renderEmpty(message) {
  return el("div", { class: "empty" }, message || "—  本时间窗暂无候选");
}

// ---------- 议题聚类 ----------
function renderCluster(cluster) {
  const main = cluster.main;
  const noFact = !cluster.main_is_fact;

  const head = el("div", { class: "cluster-head" },
    el("span", { class: "fp-tag" }, cluster.fingerprint),
    el("span", { class: "count" }, `共 ${cluster.article_count} 篇`),
    noFact ? el("span", { class: "warn-no-fact" }, "⚠️ 该议题暂无事实源（仅观点）") : null,
  );

  const mainTitleCn = (main.title_cn && main.title_cn.trim()) || main.title;
  const mainTitleEn = main.title || "";
  const showMainEn = mainTitleEn && mainTitleEn !== mainTitleCn;

  const mainBlock = el("div", { class: "cluster-main" },
    el("div", { class: `label${noFact ? " no-fact" : ""}` },
      noFact ? "💭 暂代主条（观点）" : "📋 主条（事实）"),
    el("div", { class: "title" },
      el("a", { href: main.url, target: "_blank", rel: "noopener noreferrer" }, mainTitleCn)
    ),
    showMainEn ? el("div", { class: "cluster-main-en" }, mainTitleEn) : null,
    el("div", { class: "sub" },
      `${main.source_name} · ${main.source_tier} 级 · 总分 ${main.total_score}` +
      (main.maturity_stage ? ` · ${main.maturity_stage}` : "")
    ),
    main.reason ? el("div", { class: "card-reason", style: "border-top: none; padding-top: 4px;" }, sanitize(main.reason)) : null,
  );

  // related
  const all = [...(cluster.related_facts || []), ...(cluster.related_opinions || [])];
  let relatedBlock = null;
  if (all.length > 0) {
    const items = all.map(r => {
      const tlabel = TYPE_LABEL[r.content_type] || {};
      const cls = FACT_TYPES.has(r.content_type) ? "fact" : "opinion";
      const rTitleCn = (r.title_cn && r.title_cn.trim()) || r.title;
      return el("li", { class: cls },
        el("span", { class: "ct" }, `${tlabel.icon || "•"} ${tlabel.text || r.content_type}`),
        el("a", { href: r.url, target: "_blank", rel: "noopener noreferrer", title: r.title }, rTitleCn),
        el("span", { class: "muted" }, ` · ${r.source_name} · ${r.total_score}`),
      );
    });

    relatedBlock = el("details", { class: "cluster-related", open: "" },
      el("summary", {}, `相关报道 ${all.length} 篇（点击折叠/展开）`),
      el("ul", {}, ...items),
    );
  }

  return el("div", { class: "cluster" }, head, mainBlock, relatedBlock);
}

// ---------- 主渲染 ----------
function renderTab(data, tabKey) {
  const tabData = data.tabs[tabKey] || { facts: { top3: [], pool: [] }, opinions: { top3: [], pool: [] } };

  // 双栏 Top 3
  const factsTop3 = $("#facts-top3");
  factsTop3.innerHTML = "";
  if (!tabData.facts.top3.length) {
    factsTop3.appendChild(renderEmpty("本窗口暂无 Top 事实"));
  } else {
    tabData.facts.top3.forEach(a => factsTop3.appendChild(renderCard(a)));
  }

  const opTop3 = $("#opinions-top3");
  opTop3.innerHTML = "";
  if (!tabData.opinions.top3.length) {
    opTop3.appendChild(renderEmpty("本窗口暂无 Top 观点"));
  } else {
    tabData.opinions.top3.forEach(a => opTop3.appendChild(renderCard(a)));
  }

  // 情报池
  const factsPool = $("#facts-pool");
  factsPool.innerHTML = "";
  if (!tabData.facts.pool.length) {
    factsPool.appendChild(renderEmpty("情报池为空"));
  } else {
    tabData.facts.pool.forEach(a => factsPool.appendChild(renderCard(a, { compact: true })));
  }

  const opPool = $("#opinions-pool");
  opPool.innerHTML = "";
  if (!tabData.opinions.pool.length) {
    opPool.appendChild(renderEmpty("情报池为空"));
  } else {
    tabData.opinions.pool.forEach(a => opPool.appendChild(renderCard(a, { compact: true })));
  }
}

function renderClusters(data) {
  const list = $("#clusters-list");
  list.innerHTML = "";
  const clusters = data.clusters || [];
  if (!clusters.length) {
    list.appendChild(renderEmpty("暂无多文章议题（无聚类）"));
    return;
  }
  // 议题聚类是跨时间窗的（同议题不区分窗口）
  clusters.forEach(c => list.appendChild(renderCluster(c)));
}

function renderHeader(data) {
  $("#snapshot-time").textContent = "数据更新：" + fmtDate(data.snapshot_at);
  const s = data.stats || {};
  const byType = s.by_type || {};
  const factCount = (byType.fact_legislative || 0) + (byType.fact_enforcement || 0) + (byType.fact_official_doc || 0);
  const opCount   = byType.opinion_analysis || 0;
  $("#stats-summary").textContent =
    `候选 ${s.total ?? "?"} 条 · 已 veto ${s.veto ?? 0} · 事实 ${factCount} / 观点 ${opCount}`;
}

// ---------- Tab 切换 ----------
function bindTabs(data) {
  $$(".tab").forEach(btn => {
    btn.addEventListener("click", () => {
      $$(".tab").forEach(b => b.classList.toggle("active", b === btn));
      renderTab(data, btn.dataset.tab);
    });
  });
}

// ---------- 入口 ----------
async function loadTodayJson() {
  let lastErr;
  for (const url of TODAY_JSON_CANDIDATES) {
    try {
      const resp = await fetch(url, { cache: "no-store" });
      if (resp.ok) return await resp.json();
      lastErr = new Error(`HTTP ${resp.status} at ${url}`);
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr || new Error("加载 today.json 失败");
}

async function main() {
  let data;
  try {
    data = await loadTodayJson();
  } catch (err) {
    document.body.innerHTML = `
      <div style="padding: 60px 28px; text-align: center; color: #b91c1c; font-family: -apple-system, BlinkMacSystemFont, sans-serif;">
        <h2>加载 today.json 失败</h2>
        <p>${err.message}</p>
        <p class="muted" style="color: #545d68;">
          请确认你正在通过 HTTP 访问（不是 file:// 直接打开），<br>
          且 <code>data/today.json</code> 已经生成。
        </p>
        <pre style="text-align: left; background: #f7f7f5; padding: 14px; border-radius: 6px; max-width: 560px; margin: 20px auto;">cd $(项目根)
python3 -m http.server 8765
# 访问 http://localhost:8765/v2/index.html</pre>
      </div>`;
    throw err;
  }

  renderHeader(data);
  renderClusters(data);
  // 默认选 120h（"过去 5 天"）—— mock 数据这一档候选最丰富
  const initialTab = "120h";
  renderTab(data, initialTab);
  bindTabs(data);
  updateFavBadge();   // v1.2: 初始化顶栏收藏数
  console.log("[briefing] 已加载", data);
}

main();
