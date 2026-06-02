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

// v1.4：远程同步（Vercel Serverless /api/fav）
// - 点赞改本地 localStorage 立刻生效
// - 节流 15 秒后批量同步到仓库（连点多次只触发一次网络请求）
// - 页面首次打开时自动从仓库拉取合并
const FAV_SYNC_ENDPOINT = "/api/fav";
// 跟 Vercel 环境变量 FAV_SHARED_SECRET 对齐；URL 里 ?secret= 也可带，但头更干净
// 如果 Vercel 没配 FAV_SHARED_SECRET 则这里留空即可（会被后端放行）
const FAV_SYNC_SECRET = "";
const FAV_SYNC_DEBOUNCE_MS = 15000;
let _favSyncTimer = null;
let _favSyncInFlight = false;

function scheduleRemoteSync() {
  if (!FAV_SYNC_ENDPOINT) return;
  if (_favSyncTimer) clearTimeout(_favSyncTimer);
  _favSyncTimer = setTimeout(doRemoteSync, FAV_SYNC_DEBOUNCE_MS);
}

async function doRemoteSync() {
  if (_favSyncInFlight) {
    // 正在同步，稍后重试（等当前完成）
    _favSyncTimer = setTimeout(doRemoteSync, 5000);
    return;
  }
  _favSyncInFlight = true;
  try {
    const favs = loadFavs();
    const res = await fetch(FAV_SYNC_ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(FAV_SYNC_SECRET ? { "X-Fav-Secret": FAV_SYNC_SECRET } : {}),
      },
      body: JSON.stringify({ favorites: favs, secret: FAV_SYNC_SECRET || undefined }),
    });
    if (!res.ok) {
      console.warn("[fav sync] HTTP", res.status, await res.text());
    } else {
      const data = await res.json();
      console.log("[fav sync] ok", data);
      flashSyncIndicator(data.changed ? "✅ 已同步到仓库" : "✅ 已是最新");
    }
  } catch (e) {
    console.warn("[fav sync] failed:", e);
    flashSyncIndicator("⚠️ 同步失败（已存本地）");
  } finally {
    _favSyncInFlight = false;
  }
}

/** 首次打开页面时，从仓库拉取收藏列表，合并到 localStorage（更新的为准） */
async function pullRemoteFavs() {
  if (!FAV_SYNC_ENDPOINT) return;
  try {
    const res = await fetch(FAV_SYNC_ENDPOINT, { method: "GET" });
    if (!res.ok) return;
    const data = await res.json();
    const remote = Array.isArray(data.favorites) ? data.favorites : [];
    if (!remote.length) return;

    const local = loadFavs();
    const byId = new Map();
    for (const f of local) if (f && f.id != null) byId.set(f.id, f);
    let merged = 0;
    for (const f of remote) {
      if (!f || f.id == null) continue;
      const exist = byId.get(f.id);
      if (!exist || (f.savedAt || "") > (exist.savedAt || "")) {
        byId.set(f.id, f);
        merged++;
      }
    }
    const mergedArr = Array.from(byId.values())
      .sort((a, b) => (b.savedAt || "").localeCompare(a.savedAt || ""));
    localStorage.setItem(FAV_KEY, JSON.stringify(mergedArr));
    updateFavBadge();
    if (merged > 0) {
      console.log("[fav sync] 从仓库拉取，合并 +" + merged + " 条");
    }
  } catch (e) {
    console.warn("[fav sync] pull failed:", e);
  }
}

// 同步状态小指示（右下角飘一下然后消失）
function flashSyncIndicator(text) {
  let el = document.getElementById("fav-sync-indicator");
  if (!el) {
    el = document.createElement("div");
    el.id = "fav-sync-indicator";
    el.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:9999;"
      + "background:#1f2937;color:#fff;padding:8px 14px;border-radius:8px;"
      + "font-size:12.5px;opacity:0;transition:opacity 0.25s;pointer-events:none;";
    document.body.appendChild(el);
  }
  el.textContent = text;
  el.style.opacity = "1";
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.style.opacity = "0"; }, 2000);
}

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

// v1.4：title_cn 规范化
// LLM 偶尔没归纳出中文标题，或 KtN 邮件的通用英文标题被当 title_cn 存了
// 前端展示时兜底：如果 title_cn 是英文通用标题，从 summary 扒一段替代
// v1.5 fix：额外检测普通英文句子（以大写冠词/介词开头、含 "the/and/of/to/in/for" 等），这类也是 LLM 放错位置
function normalizeTitleCn(article) {
  const raw = (article.title_cn || "").trim();
  const enTitle = article.title || "";

  // 已知 newsletter 通用标题
  const isNewsletterGeneric =
    /^New from DataGuidance/i.test(raw) ||
    /^Daily Dashboard/i.test(raw) ||
    /^Daily Briefing/i.test(raw) ||
    /newsletter$/i.test(raw);

  // 英文句子特征：以大写冠词/介词/代词开头，含英文常见虚词组合（LLM 把英文 title 当 title_cn 存的误判）
  // 例如："The UK Information Commissioner's Office updated its guidance..."
  // 匹配：开头大写 + 含 " the " / " and " / " of " / " to " / " for " / " in " 等英文常见词
  const isEnglishSentence = /^(The |A |An |This |That |These |Those |On |In |At |For |With |By |From |To |About )[A-Z]/.test(raw)
    && /\b(the|and|of|to|in|for|with|by|from|that|this|which|their|its)\b/i.test(raw);

  const isBadTitleCn =
    !raw ||
    raw === enTitle ||
    raw.length < 4 ||
    isNewsletterGeneric ||
    isEnglishSentence;  // 新增：英文句子也当坏标题处理

  if (!isBadTitleCn) return { titleCn: raw, isFallback: false };

  // 有 summary → 取前 60 字符；中文摘要不加 [未归纳] 前缀（已有人工归纳），英文 summary 才加
  if (article.summary) {
    const hint = article.summary.slice(0, 60).replace(/\s+/g, " ").trim();
    const looksChinese = /[\u4e00-\u9fff]/.test(hint);
    const display = looksChinese ? hint : "[未归纳] " + hint;
    return { titleCn: display + "…", isFallback: true };
  }
  return { titleCn: enTitle || "(无标题)", isFallback: true };
}

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
  scheduleRemoteSync();  // v1.4: 节流触发远程同步
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

  // 中文归纳标题 + 英文原题（v1.4 统一走 normalizeTitleCn 兜底）
  const { titleCn, isFallback } = normalizeTitleCn(article);
  const titleEn = article.title || "";
  const showEnglishLine = titleEn && titleEn !== titleCn && !isFallback;

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
  const favList = loadFavs();
  const cur = fbMap[article.id];
  // v1.4 fix：拇指 active 要同时看 feedback 和 favorites（跨设备同步后 favorites 有但 feedback 可能没有）
  const isFaved = favList.some(f => f.id === article.id);
  const upBtn = el("button", {
    class: `fb-btn up${cur === 1 || isFaved ? " active" : ""}`,
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

  const { titleCn: mainTitleCn, isFallback: mainFallback } = normalizeTitleCn(main);
  const mainTitleEn = main.title || "";
  const showMainEn = mainTitleEn && mainTitleEn !== mainTitleCn && !mainFallback;

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
      const { titleCn: rTitleCn } = normalizeTitleCn(r);
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

  // v1.4：当期热点主题（按时间窗随 tab 切换）
  renderTopicsForWindow(data, tabKey);

  // 情报池
  const factsPool = $("#facts-pool");
  factsPool.innerHTML = "";
  renderPool(factsPool, tabData.facts.pool, "facts");

  const opPool = $("#opinions-pool");
  opPool.innerHTML = "";
  renderPool(opPool, tabData.opinions.pool, "opinions");
}

// v1.4：渲染当前时间窗的 LLM 主题聚类
function renderTopicsForWindow(data, tabKey) {
  const list = $("#topics-list");
  if (!list) return;
  list.innerHTML = "";

  const topicsByWindow = data.topics_by_window || {};
  const topics = topicsByWindow[tabKey] || [];

  if (!topics.length) {
    list.appendChild(renderEmpty("当前时间窗暂无聚类结果（候选文章过少或 LLM 未生成）"));
    return;
  }

  // 建个 id → article 的索引（合并所有 tab 的 top3 + pool）
  const idToArticle = {};
  for (const tk of Object.keys(data.tabs || {})) {
    const tab = data.tabs[tk];
    for (const col of ["facts", "opinions"]) {
      for (const src of ["top3", "pool"]) {
        for (const a of (tab[col]?.[src] || [])) {
          idToArticle[a.id] = a;
        }
      }
    }
  }

  topics.forEach(t => list.appendChild(renderTopic(t, idToArticle)));
}

// 后端 /api/generate-tip 单次最多接受的文章数（与 api/generate-tip.js 的 max 12 保持一致）
const TOPIC_INSIGHT_MAX = 12;

function renderTopic(topic, idToArticle) {
  const articles = (topic.article_ids || [])
    .map(id => idToArticle[id])
    .filter(Boolean);

  const head = el("div", { class: "topic-head" },
    el("span", { class: "topic-emoji" }, topic.emoji || "📌"),
    el("span", { class: "topic-name" }, topic.name || "未命名主题"),
    el("span", { class: "topic-count" }, `${articles.length} 篇`),
    topic.blurb
      ? el("span", { class: "topic-blurb muted" }, `· ${topic.blurb}`)
      : null,
  );

  // ---- v1.8：在每条文章前加 checkbox，让用户挑选要送进 LLM 的素材 ----
  // 默认全部不勾选——用户自己来挑；后端硬上限 12 条
  const showCheckbox = articles.length >= 2;
  const checkboxes = [];   // 与 articles 同序，方便统计

  const listEl = el("ul", { class: "topic-articles" });
  articles.forEach((a, idx) => {
    const { titleCn } = normalizeTitleCn(a);
    const typeMeta = TYPE_LABEL[a.content_type] || {};

    let checkbox = null;
    if (showCheckbox) {
      checkbox = el("input", {
        type: "checkbox",
        class: "topic-article-pick",
        title: "勾选则纳入「写成一段洞察」（最多 " + TOPIC_INSIGHT_MAX + " 条）",
      });
      // 默认全部不勾选——交给用户自己点
      checkbox.checked = false;
      checkboxes.push(checkbox);
    }

    const li = el("li", { class: FACT_TYPES.has(a.content_type) ? "fact" : "opinion" });
    if (checkbox) li.appendChild(checkbox);
    li.appendChild(el("span", { class: "topic-score" }, String(a.total_score)));
    li.appendChild(el("span", { class: "topic-type" }, typeMeta.icon || "•"));
    li.appendChild(el("a", { href: a.url, target: "_blank", rel: "noopener noreferrer", title: a.title }, titleCn));
    li.appendChild(el("span", { class: "muted small" }, ` · ${a.source_name}`));
    listEl.appendChild(li);
  });

  // ---- v1.7/v1.8：一键洞察按钮 + 就地展开结果区 ----
  const card = el("div", { class: "topic-card" }, head, listEl);
  if (showCheckbox) {
    const insightBox = el("div", { class: "topic-insight-box" });  // 默认空，按钮触发后填

    const btn = el("button", {
      class: "topic-insight-btn",
    }, "");

    // 复用：根据当前勾选数刷新按钮文案 / disabled 状态 / title 提示
    const refreshBtn = () => {
      const picked = checkboxes.filter(cb => cb.checked).length;
      btn.dataset.picked = String(picked);
      if (picked === 0) {
        btn.disabled = true;
        btn.textContent = "✨ 写成一段洞察（请先勾选至少 1 条）";
        btn.title = "请在上方文章列表里至少勾选 1 条";
      } else if (picked > TOPIC_INSIGHT_MAX) {
        btn.disabled = true;
        btn.textContent = `✨ 写成一段洞察（已选 ${picked} 条，超过上限 ${TOPIC_INSIGHT_MAX}）`;
        btn.title = `最多只能勾选 ${TOPIC_INSIGHT_MAX} 条；请取消多余勾选`;
      } else {
        btn.disabled = false;
        btn.textContent = `✨ 写成一段洞察（已选 ${picked} 条）`;
        btn.title = `把已勾选的 ${picked} 条素材送进 AI，写一段 300-500 字深度洞察（chat 模型，约 ¥0.01-0.03 / 次）`;
      }
    };
    refreshBtn();
    checkboxes.forEach(cb => cb.addEventListener("change", refreshBtn));

    btn.addEventListener("click", () => {
      // 收集当前勾选的 articles，按原始顺序传给后端
      const picked = articles.filter((_, i) => checkboxes[i].checked);
      if (!picked.length) return;
      if (picked.length > TOPIC_INSIGHT_MAX) return;
      runTopicInsight(topic, picked, btn, insightBox, refreshBtn);
    });

    const footer = el("div", { class: "topic-actions" }, btn);
    card.appendChild(footer);
    card.appendChild(insightBox);
  }
  return card;
}

// v1.7：调 /api/generate-tip 给 topic 写一段 300-500 字洞察
// 复用工作台 endpoint + 同款 prompt + 文风注入
// v1.8：articles 是用户已勾选的子集；refreshBtn 用于在结束后把按钮文案重置为
//       「✨ 重新生成（已选 N 条）」/ disabled 状态由 refreshBtn 重新计算
async function runTopicInsight(topic, articles, btn, box, refreshBtn) {
  if (btn.disabled) return;
  btn.disabled = true;
  const startedAt = Date.now();

  // 状态条
  let timer = setInterval(() => {
    const sec = Math.round((Date.now() - startedAt) / 1000);
    btn.textContent = `⏳ 生成中 ${sec}s（约 8-15 秒）`;
  }, 1000);

  box.innerHTML = "";
  box.appendChild(el("div", { class: "topic-insight-status muted" },
    "✨ 正在调用 Tavily 补全 + DeepSeek 撰写……"));

  try {
    // 与工作台保持一致的 payload 结构
    const payload = {
      articles: articles.map(a => ({
        id: a.id,
        title: a.title,
        title_cn: a.title_cn || "",
        url: a.url,
        summary: a.summary || "",
        source_name: a.source_name,
        source_tier: a.source_tier,
        content_type: a.content_type,
        published_at: a.published_at,
        reason: a.reason || "",
      })),
      // 给 AI 一个隐性的"主题导览"（topic.name + blurb）作为补充判断
      // 让它知道这一批素材在用户视角下是同一个主题
      userNote: topic.name
        ? `（这一批素材属于「${topic.name}${topic.blurb ? " — " + topic.blurb : ""}」主题，请围绕这条主线展开）`
        : "",
      model: "chat",  // 安全默认（reasoner 已 fallback 到 chat）
      doSearch: true,
    };

    const res = await fetch("/api/generate-tip", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Tips-Secret": "zsGuJlOkyk_lFBF9WFRTOz2WslKF7RqG",
      },
      body: JSON.stringify(payload),
    });

    clearInterval(timer);

    if (!res.ok) {
      const errText = await res.text().catch(() => res.statusText);
      throw new Error(`HTTP ${res.status}: ${errText.slice(0, 200)}`);
    }
    const data = await res.json();
    const tip = data.tip || "（生成结果为空）";
    const cost = data.costEstimateCny != null ? `¥${data.costEstimateCny.toFixed(3)}` : "";
    const elapsed = ((data.elapsedMs || (Date.now() - startedAt)) / 1000).toFixed(1);

    // 渲染结果
    box.innerHTML = "";

    const searchLog = Array.isArray(data.searchLog) ? data.searchLog : [];
    const okSearches = searchLog.filter(s => s.ok).length;
    const failSearches = searchLog.length - okSearches;

    box.appendChild(el("div", { class: "topic-insight-meta muted" },
      `生成完成 · ${data.model || "chat"} · ${elapsed}s · ${cost} · ${articles.length} 条素材 + ${okSearches} 次 Tavily 搜索${failSearches ? `（含 ${failSearches} 次失败）` : ""}`));

    // v1.8：可折叠的 Tavily 搜索详情——证明确实调了，且能看到每条素材发了什么 query、命中几条、耗时多少
    if (searchLog.length) {
      const details = el("details", { class: "topic-insight-search-log" });
      const summary = el("summary", {},
        `🔍 查看 ${searchLog.length} 次 Tavily 搜索详情（点击展开）`,
      );
      details.appendChild(summary);
      const ul = el("ul", { class: "topic-insight-search-list" });
      searchLog.forEach((s, i) => {
        const status = s.ok
          ? `✅ ${s.nResults || 0} 条结果 · ${s.elapsedMs || 0}ms`
          : `❌ 失败：${(s.error || "未知错误").slice(0, 80)}`;
        const li = el("li", {},
          el("div", { class: "search-title" }, `${i + 1}. ${s.title || "(无标题)"}`),
          el("div", { class: "search-query muted small" }, `query: ${s.query || ""}`),
          el("div", { class: "search-status small", style: s.ok ? "color: var(--c-steel-deep)" : "color: var(--c-vermillion)" }, status),
        );
        ul.appendChild(li);
      });
      details.appendChild(ul);
      box.appendChild(details);
    }

    const tipEl = el("div", { class: "topic-insight-text" });
    renderTipWithBold(tipEl, tip);
    box.appendChild(tipEl);
    const actions = el("div", { class: "topic-insight-actions" },
      mkBtn("📋 复制", () => {
        navigator.clipboard.writeText(tip).then(
          () => flash(actions, "已复制"),
          () => flash(actions, "复制失败"),
        );
      }),
      mkBtn("💾 存到工作台历史", () => {
        saveInsightToWorkbenchHistory(topic, articles, tip, data, payload);
        flash(actions, "已存到工作台历史");
      }),
      mkBtn("🗑️ 弃用", () => {
        box.innerHTML = "";
        btn.disabled = false;
        if (typeof refreshBtn === "function") {
          refreshBtn();   // 让按钮文案恢复成「✨ 写成一段洞察（已选 N 条）」
        } else {
          btn.textContent = "✨ 写成一段洞察";
        }
      }),
    );
    box.appendChild(actions);

    // 生成完后，按当前勾选数刷新按钮文案为「✨ 重新生成（已选 N 条）」
    if (typeof refreshBtn === "function") {
      refreshBtn();
      // refreshBtn 默认前缀是「✨ 写成一段洞察」，这里覆盖成「✨ 重新生成」
      // （只改文案前缀，状态保持 refreshBtn 计算结果）
      if (!btn.disabled) {
        const picked = btn.dataset.picked || articles.length;
        btn.textContent = `✨ 重新生成（已选 ${picked} 条）`;
      }
    } else {
      btn.textContent = "✨ 重新生成";
      btn.disabled = false;
    }
  } catch (e) {
    clearInterval(timer);
    box.innerHTML = "";
    box.appendChild(el("div", { class: "topic-insight-error" },
      "❌ 生成失败：", String(e.message || e)));
    if (typeof refreshBtn === "function") {
      refreshBtn();
      if (!btn.disabled) {
        const picked = btn.dataset.picked || articles.length;
        btn.textContent = `✨ 重试（已选 ${picked} 条）`;
      }
    } else {
      btn.textContent = "✨ 重试";
      btn.disabled = false;
    }
  }
}

function mkBtn(text, onClick) {
  const b = el("button", { class: "topic-insight-action-btn" }, text);
  b.addEventListener("click", onClick);
  return b;
}

// v1.8：把 LLM 输出的 markdown 极简版渲染到目标节点
//   - 只支持 **xxx** 加粗（其他 md 语法原样保留为文本）
//   - 段落用 \n\n 分隔；段内 \n 转 <br>
//   - 全部走 textNode，无 innerHTML，无 XSS 风险
//   - 同时统计 bold count，超过 5 处会在 console 警告（方便用户/我后续观察）
function renderTipWithBold(host, raw) {
  if (!raw) return;
  const text = String(raw).replace(/\r\n/g, "\n").trim();
  // 段落切分：\n\n+ 视为新段
  const paragraphs = text.split(/\n{2,}/);
  let boldCount = 0;

  paragraphs.forEach((para, pIdx) => {
    const p = el("p", { class: "topic-insight-paragraph" });
    // 段内的 **xxx**（最短匹配，禁止跨行）
    const regex = /\*\*([^*\n]+?)\*\*/g;
    let lastIndex = 0;
    let m;
    while ((m = regex.exec(para)) !== null) {
      // 加粗前的文本（含可能的换行）
      const before = para.slice(lastIndex, m.index);
      appendTextWithBr(p, before);
      // 加粗内容
      const strong = el("strong", { class: "topic-insight-bold" }, m[1]);
      p.appendChild(strong);
      boldCount += 1;
      lastIndex = m.index + m[0].length;
    }
    // 段落剩余尾巴
    appendTextWithBr(p, para.slice(lastIndex));
    host.appendChild(p);
  });

  if (boldCount > 12) {
    console.warn(`[insight] 加粗 ${boldCount} 处，超过约束的 12 处上限——LLM 加粗过多，可考虑「重新生成」`);
  } else if (boldCount < 4) {
    console.warn(`[insight] 加粗仅 ${boldCount} 处，低于建议下限 4 处——可点「重新生成」试试`);
  }
}

// 把含 \n 的纯文本切成 textNode + <br> 序列追加到容器
function appendTextWithBr(host, str) {
  if (!str) return;
  const lines = str.split("\n");
  lines.forEach((line, i) => {
    if (i > 0) host.appendChild(el("br"));
    if (line) host.appendChild(document.createTextNode(line));
  });
}

function flash(host, text) {
  const f = el("span", { class: "topic-insight-flash" }, " " + text);
  host.appendChild(f);
  setTimeout(() => f.remove(), 1600);
}

function saveInsightToWorkbenchHistory(topic, articles, tip, apiData, payload) {
  // 与工作台 workbench.js 同样的 storage key & schema
  const KEY = "briefing.tips.v1";
  let arr = [];
  try {
    arr = JSON.parse(localStorage.getItem(KEY) || "[]");
    if (!Array.isArray(arr)) arr = [];
  } catch (_) { arr = []; }

  arr.unshift({
    id: "topic_" + Date.now(),
    generatedAt: new Date().toISOString(),
    model: apiData.model || "chat",
    elapsedMs: apiData.elapsedMs || 0,
    costEstimateCny: apiData.costEstimateCny || 0,
    userNote: payload.userNote || "",
    sources: articles.map(a => ({
      id: a.id, title: a.title, url: a.url, source_name: a.source_name,
    })),
    tip,
    triggeredFrom: "topic:" + (topic.name || "?"),
  });
  if (arr.length > 100) arr.length = 100;
  localStorage.setItem(KEY, JSON.stringify(arr));
}

// v1.2：pool 长时间窗下会很长，默认只展 10 条，点按钮展开剩余
const POOL_INITIAL_SHOW = 10;

function renderPool(container, pool, columnKind) {
  if (!pool || !pool.length) {
    container.appendChild(renderEmpty("情报池为空"));
    return;
  }

  const initial = pool.slice(0, POOL_INITIAL_SHOW);
  const rest    = pool.slice(POOL_INITIAL_SHOW);

  initial.forEach(a => container.appendChild(renderCard(a, { compact: true })));

  if (rest.length === 0) return;

  // 剩余卡片先渲染但隐藏
  const hiddenWrap = el("div", { class: "pool-hidden", style: "display: none;" });
  rest.forEach(a => hiddenWrap.appendChild(renderCard(a, { compact: true })));
  container.appendChild(hiddenWrap);

  // 展开按钮
  const toggleBtn = el("button", {
    class: "pool-toggle",
    type: "button",
  }, `▼ 展开剩余 ${rest.length} 条（${columnKind === "facts" ? "事实" : "观点"}）`);
  let expanded = false;
  toggleBtn.addEventListener("click", () => {
    expanded = !expanded;
    hiddenWrap.style.display = expanded ? "" : "none";
    toggleBtn.textContent = expanded
      ? `▲ 收起 ${rest.length} 条`
      : `▼ 展开剩余 ${rest.length} 条（${columnKind === "facts" ? "事实" : "观点"}）`;
  });
  container.appendChild(toggleBtn);
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

// v1.5 → v1.6 升级：检测「数据陈旧」而非「流水线没跑」
//
// 历史教训（2026-05-09 ~ 2026-05-29）：
// 老逻辑用 last_run_at（流水线跑完时间）判断陈旧，但 LLM 没钱时流水线还会"绿绿地跑完"
// 一份内容空的 today.json，last_run_at 每天刷新但实际数据 20 天没变。前端永远不弹红条。
//
// 新逻辑：优先看 latest_article_published_at（库里最新文章发布时间），> 36h 才弹。
// fallback 到老的 last_run_at + 18h（兼容旧版 last_run.json）。
const STALE_DATA_THRESHOLD_HOURS = 36;     // 新：库里最新文章 > 36h 触发
const STALE_RUN_THRESHOLD_HOURS = 18;      // 老 fallback：last_run_at > 18h
const LAST_RUN_CANDIDATES = [
  "/data/last_run.json",
  "../data/last_run.json",
  "./data/last_run.json",
];
async function checkLastRunFreshness() {
  let lastRunData = null;
  for (const url of LAST_RUN_CANDIDATES) {
    try {
      const resp = await fetch(url, { cache: "no-store" });
      if (resp.ok) { lastRunData = await resp.json(); break; }
    } catch (_) { /* 继续尝试下一个 */ }
  }
  if (!lastRunData) return;

  // === 新逻辑（v1.6）：优先看 latest_article_published_at ===
  const latestPubIso = lastRunData.latest_article_published_at;
  if (latestPubIso) {
    const latestPub = new Date(latestPubIso);
    if (!isNaN(latestPub.getTime())) {
      const hoursAgo = (Date.now() - latestPub.getTime()) / 36e5;
      if (hoursAgo >= STALE_DATA_THRESHOLD_HOURS) {
        renderStaleBanner({
          hoursAgo,
          mode: "data",
          extra: lastRunData,
        });
      }
      return;  // 已用新逻辑，不再走老 fallback
    }
  }

  // === 老 fallback（v1.5）：旧 last_run.json 没 latest_article_published_at 字段时 ===
  if (!lastRunData.last_run_at) return;
  const lastRun = new Date(lastRunData.last_run_at);
  if (isNaN(lastRun.getTime())) return;
  const hoursAgo = (Date.now() - lastRun.getTime()) / 36e5;
  if (hoursAgo < STALE_RUN_THRESHOLD_HOURS) return;
  renderStaleBanner({ hoursAgo, mode: "run", extra: lastRunData });
}

function renderStaleBanner({ hoursAgo, mode, extra }) {
  const banner = document.createElement("div");
  banner.className = "stale-run-banner";
  const hoursText = hoursAgo >= 24
    ? `${Math.floor(hoursAgo / 24)} 天 ${Math.round(hoursAgo % 24)} 小时`
    : `${Math.round(hoursAgo)} 小时`;
  // 文案根据 mode 区分：mode=data 表示数据陈旧（核心问题），mode=run 表示流水线漏跑
  const message = mode === "data"
    ? `库里最新文章已经是 <strong>${hoursText}</strong>前的（数据陈旧，流水线可能跑了但没新评分文章入库）`
    : `数据已经 <strong>${hoursText}</strong> 没有更新（GitHub Actions cron 可能被跳过了）`;
  banner.innerHTML = `
    <span class="stale-icon">⚠️</span>
    <span class="stale-text">
      ${message}。
      <a href="https://github.com/SUNXIAOMEI-YUFEI/network-governance-briefing/actions/workflows/daily.yml"
         target="_blank" rel="noopener">前往 GitHub Actions ↗</a>
    </span>
    <button class="stale-close" aria-label="关闭">×</button>
  `;
  document.body.insertBefore(banner, document.body.firstChild);
  banner.querySelector(".stale-close").addEventListener("click", () => banner.remove());
  console.warn("[briefing] 数据陈旧告警", { mode, hoursAgo, ...extra });
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
  // v1.6：默认选 360h（过去 15 天）作为兜底
  // 历史教训：之前默认 120h（过去 5 天），2026-05 流水线挂了 20 天后，
  // 用户打开网页 4 个 tab 全空，因为 5/9 数据落不进 5/24-5/29 这个窗口。
  // 改成 360h 后，即使将来再有 7-10 天故障，至少第一眼能看到内容。
  const initialTab = "360h";
  renderTab(data, initialTab);
  bindTabs(data);
  updateFavBadge();   // v1.2: 初始化顶栏收藏数
  pullRemoteFavs();   // v1.4: 异步从仓库拉取合并收藏列表
  checkLastRunFreshness();  // v1.5: 异步检测漏跑，超 18h 弹顶部红条
  console.log("[briefing] 已加载", data);
}

main();
