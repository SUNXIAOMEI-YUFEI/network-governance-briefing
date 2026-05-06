/* 我的收藏（localStorage 方案）
 * 存储 key: "briefing.favorites.v1"
 * 结构: [{article..., savedAt: ISO 时间戳}]
 */

const FAV_KEY = "briefing.favorites.v1";

const TYPE_LABEL = {
  fact_legislative:  { icon: "📋", text: "立法事实" },
  fact_enforcement:  { icon: "⚖️", text: "执法事实" },
  fact_official_doc: { icon: "📄", text: "官方文件" },
  opinion_analysis:  { icon: "💭", text: "观点分析" },
};
const FACT_TYPES = new Set(["fact_legislative", "fact_enforcement", "fact_official_doc"]);

// 对外展示脱敏：与 app.js 保持一致
const SANITIZE_MAP = [
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

const $  = (sel, root = document) => root.querySelector(sel);

function loadFavs() {
  try { return JSON.parse(localStorage.getItem(FAV_KEY) || "[]"); }
  catch { return []; }
}
function saveFavs(arr) {
  localStorage.setItem(FAV_KEY, JSON.stringify(arr));
}

function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("zh-CN", { hour12: false,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}

function fmtDate(iso) {
  if (!iso) return "";
  try { return new Date(iso).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" }); }
  catch { return ""; }
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

function renderFavCard(fav, onRemove) {
  const a = fav;
  const typeMeta = TYPE_LABEL[a.content_type] || { icon: "•", text: a.content_type };

  const score = el("div", { class: "card-score" },
    el("span", { class: "num" }, String(a.total_score ?? "—")),
    el("span", { class: "label" }, "TOTAL")
  );

  const titleCn = (a.title_cn && a.title_cn.trim()) || a.title || "(无标题)";
  const titleEn = a.title || "";
  const showEn = titleEn && titleEn !== titleCn;

  const titleBlock = el("div", { class: "card-title-block" },
    el("a", {
      class: "card-title-cn",
      href: a.url,
      target: "_blank",
      rel: "noopener noreferrer",
      title: titleEn || titleCn,
    }, titleCn),
    showEn ? el("div", { class: "card-title-en" }, titleEn) : null,
  );

  const badges = el("div", { class: "card-badges" });
  badges.appendChild(el("span", { class: `badge badge-type-${a.content_type}` },
    `${typeMeta.icon} ${typeMeta.text}`));
  badges.appendChild(el("span", { class: `badge badge-tier-${a.source_tier}` },
    a.source_tier + " 级"));
  if (a.maturity_stage) {
    badges.appendChild(el("span", { class: "badge badge-stage" }, a.maturity_stage));
  }
  for (const ax of (a.anxiety_hits || []).slice(0, 2)) {
    badges.appendChild(el("span", { class: "badge badge-anxiety" }, sanitize(ax)));
  }

  const reason = a.reason ? el("div", { class: "card-reason" }, sanitize(a.reason)) : null;

  const meta = el("div", { class: "card-meta" },
    el("span", {}, a.source_name),
    el("span", { class: "muted" }, "·"),
    el("span", {}, `发布 ${fmtDate(a.published_at)}`),
    el("span", { class: "muted" }, "·"),
    el("span", { class: "muted" }, `收藏 ${fmtTime(a.savedAt)}`),
    el("span", { class: "muted" }, "·"),
    el("a", { href: a.url, target: "_blank", rel: "noopener noreferrer" }, "原文 ↗"),
  );

  const removeBtn = el("button", {
    class: "fb-btn remove",
    title: "从收藏夹移除",
    onclick: () => {
      if (confirm(`确定从收藏夹移除？\n\n「${titleCn}」`)) {
        onRemove(a.id);
      }
    },
  }, "✕");
  const feedback = el("div", { class: "feedback" }, removeBtn);

  const column = FACT_TYPES.has(a.content_type) ? "column-facts" : "column-opinions";
  return el("article", { class: `card ${column}` },
    score, titleBlock, badges, reason, meta, feedback
  );
}

function render() {
  const favs = loadFavs();
  const list = $("#fav-list");
  list.innerHTML = "";

  $("#fav-count-meta").textContent =
    favs.length === 0 ? "暂无收藏" : `共 ${favs.length} 条收藏`;

  if (favs.length === 0) {
    list.appendChild(el("div", { class: "empty" },
      "还没有收藏。去工作台点几个 👍，它们会自动出现在这里。"));
    return;
  }

  // 按 savedAt 降序（最近收的在前）
  const sorted = [...favs].sort((a, b) =>
    (b.savedAt || "").localeCompare(a.savedAt || ""));

  sorted.forEach(fav => {
    list.appendChild(renderFavCard(fav, (idToRemove) => {
      const next = loadFavs().filter(f => f.id !== idToRemove);
      saveFavs(next);
      render();
    }));
  });
}

function exportFavs() {
  const favs = loadFavs();
  if (favs.length === 0) { alert("还没有收藏，没什么可以导出的"); return; }
  const blob = new Blob(
    [JSON.stringify({ exportedAt: new Date().toISOString(), favorites: favs }, null, 2)],
    { type: "application/json" },
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `briefing-favorites-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function importFavs(file) {
  const reader = new FileReader();
  reader.onload = (ev) => {
    try {
      const parsed = JSON.parse(ev.target.result);
      const incoming = Array.isArray(parsed.favorites) ? parsed.favorites
                     : Array.isArray(parsed) ? parsed
                     : null;
      if (!incoming) { alert("文件格式不对——找不到 favorites 数组"); return; }

      const cur = loadFavs();
      const curIds = new Set(cur.map(f => f.id));
      const added = incoming.filter(f => f && f.id && !curIds.has(f.id));

      if (added.length === 0) {
        alert(`导入完成：没有新收藏（这份 ${incoming.length} 条全部已存在）`);
        return;
      }

      saveFavs([...cur, ...added]);
      alert(`导入完成：新增 ${added.length} 条收藏（合并去重后）`);
      render();
    } catch (e) {
      alert("导入失败：" + e.message);
    }
  };
  reader.readAsText(file);
}

function bind() {
  $("#export-btn").addEventListener("click", (e) => { e.preventDefault(); exportFavs(); });
  $("#import-btn").addEventListener("click", (e) => { e.preventDefault(); $("#import-file").click(); });
  $("#import-file").addEventListener("change", (e) => {
    const f = e.target.files && e.target.files[0];
    if (f) importFavs(f);
    e.target.value = "";  // 允许重复导入同一文件
  });
}

render();
bind();
