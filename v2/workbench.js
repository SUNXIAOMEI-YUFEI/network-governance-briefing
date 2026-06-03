/* 写作工作台
 *
 * 数据流：
 *   1. 从 localStorage briefing.favorites.v1 读用户收藏池
 *   2. 用户勾选 N 条 + 可选补充 + 选模型
 *   3. 调 /api/generate-tip Vercel function（它会调 Tavily + DeepSeek）
 *   4. 生成结果展示在右侧；可"保存到历史"或"弃用"
 *   5. 历史存 localStorage briefing.tips.v1（本机；MVP 阶段不云同步）
 *
 * 关键约束：
 *   - 勾选状态是临时的（关闭浏览器即丢，按用户敲定的"选项 C"）
 *   - 历史 tips 长期存（用户敲定要"保留生成历史"）
 *   - 模型默认 reasoner，速度模式 chat
 */

const FAV_KEY = "briefing.favorites.v1";
const TIPS_HISTORY_KEY = "briefing.tips.v1";
const GENERATE_ENDPOINT = "/api/generate-tip";
// 与 Vercel 环境变量 TIPS_SHARED_SECRET 保持一致。
// 注：这是个人工作台，secret 只是防止匿名访问者随手烧 LLM 调用的薄护栏，
//     不是真正的机密——任何打开你域名的人都能在浏览器看到它，但实际风险等同于
//     "你的项目域名直接公开"，可接受。如果未来要做正经多用户产品，必须改 OAuth/JWT。
const TIPS_SECRET = "zsGuJlOkyk_lFBF9WFRTOz2WslKF7RqG";

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => root.querySelectorAll(sel);

// ============================================================
// localStorage
// ============================================================

function loadFavs() {
  try { return JSON.parse(localStorage.getItem(FAV_KEY) || "[]"); }
  catch { return []; }
}

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(TIPS_HISTORY_KEY) || "[]"); }
  catch { return []; }
}

function saveHistory(arr) {
  localStorage.setItem(TIPS_HISTORY_KEY, JSON.stringify(arr));
}

// ============================================================
// 工具
// ============================================================

const TYPE_LABEL = {
  fact_legislative:  { icon: "📋", text: "立法事实" },
  fact_enforcement:  { icon: "⚖️", text: "执法事实" },
  fact_official_doc: { icon: "📄", text: "官方文件" },
  opinion_analysis:  { icon: "💭", text: "观点分析" },
};
const FACT_TYPES = new Set(["fact_legislative", "fact_enforcement", "fact_official_doc"]);

// 与 favorites.js 保持一致的标题归一化
function normalizeTitleCn(article) {
  const raw = (article.title_cn || "").trim();
  const enTitle = article.title || "";
  const isBad =
    !raw ||
    raw === enTitle ||
    raw.length < 4 ||
    /^New from DataGuidance/i.test(raw) ||
    /^Daily Dashboard/i.test(raw) ||
    /^Daily Briefing/i.test(raw) ||
    /newsletter$/i.test(raw);
  if (!isBad) return raw;
  if (article.summary) {
    const hint = article.summary.slice(0, 60).replace(/\s+/g, " ").trim();
    return "[未归纳] " + hint + "…";
  }
  return enTitle || "(无标题)";
}

const SANITIZE_MAP = [
  [/中央网信办关切/g, "政策关切议题"],
  [/网信办关切/g, "政策关切议题"],
  [/网信办自身执法/g, "国内主管部门自身执法"],
  [/网信办/g, "监管者"],
  [/8\s*大焦虑点/g, "关切议题清单"],
  [/八大焦虑点/g, "关切议题清单"],
  [/焦虑点/g, "关切议题"],
  [/涉企黑嘴/g, "涉企舆论风险"],
  [/《网络治理动态速递》/g, "内部情报简报"],
  [/网络治理动态速递/g, "内部情报简报"],
  [/速递/g, "简报"],
];
function sanitize(text) {
  if (!text) return text;
  let s = String(text);
  for (const [re, rep] of SANITIZE_MAP) s = s.replace(re, rep);
  return s;
}

function fmtDate(iso) {
  if (!iso) return "";
  try { return new Date(iso).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" }); }
  catch { return ""; }
}
function fmtDateTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("zh-CN", {
      hour12: false, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
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

// ============================================================
// 状态
// ============================================================

const state = {
  selectedIds: new Set(),         // 当前勾选的 article id（临时态）
  currentResult: null,            // 最近一次 generate 返回的 result（未保存到历史前）
  generating: false,
  timeFilter: "all",
  searchKeyword: "",
};

// ============================================================
// 收藏池渲染
// ============================================================

function filterFavs(favs) {
  // 时间窗
  let arr = favs;
  if (state.timeFilter !== "all") {
    const hours = parseInt(state.timeFilter, 10);
    const cutoff = Date.now() - hours * 3600 * 1000;
    arr = arr.filter(f => {
      if (!f.published_at) return true;  // 没时间的不过滤
      const t = new Date(f.published_at).getTime();
      return !isNaN(t) && t >= cutoff;
    });
  }
  // 关键词
  const kw = state.searchKeyword.trim().toLowerCase();
  if (kw) {
    arr = arr.filter(f => {
      const titleCn = (f.title_cn || "").toLowerCase();
      const titleEn = (f.title || "").toLowerCase();
      const source = (f.source_name || "").toLowerCase();
      return titleCn.includes(kw) || titleEn.includes(kw) || source.includes(kw);
    });
  }
  // 默认按 savedAt 倒序
  arr = [...arr].sort((a, b) => (b.savedAt || "").localeCompare(a.savedAt || ""));
  return arr;
}

function renderPoolItem(fav) {
  const titleCn = normalizeTitleCn(fav);
  const typeMeta = TYPE_LABEL[fav.content_type] || { icon: "•", text: fav.content_type };
  const isSelected = state.selectedIds.has(fav.id);

  const checkbox = el("input", {
    type: "checkbox",
    class: "wb-pool-check",
    "data-id": fav.id,
  });
  if (isSelected) checkbox.checked = true;
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) {
      if (state.selectedIds.size >= 12) {
        alert("最多勾选 12 条素材（避免 prompt 太长）");
        checkbox.checked = false;
        return;
      }
      state.selectedIds.add(fav.id);
    } else {
      state.selectedIds.delete(fav.id);
    }
    renderSelectedPanel();
    refreshGenerateBtn();
  });

  const titleLink = el("a", {
    class: "wb-pool-title",
    href: fav.url,
    target: "_blank",
    rel: "noopener noreferrer",
    title: fav.title || "",
  }, titleCn);

  const badges = el("div", { class: "wb-pool-badges" },
    el("span", { class: `badge badge-type-${fav.content_type}` },
      `${typeMeta.icon} ${typeMeta.text}`),
    el("span", { class: `badge badge-tier-${fav.source_tier}` },
      (fav.source_tier || "C") + " 级"),
    el("span", { class: "wb-pool-source muted" },
      `${fav.source_name || ""} · ${fmtDate(fav.published_at)}`),
  );

  const reason = fav.reason
    ? el("div", { class: "wb-pool-reason muted" }, sanitize(fav.reason))
    : null;

  const column = FACT_TYPES.has(fav.content_type) ? "wb-pool-fact" : "wb-pool-opinion";

  return el("label", { class: `wb-pool-item ${column}${isSelected ? " selected" : ""}`,
                       "data-id": fav.id },
    checkbox,
    el("div", { class: "wb-pool-content" }, titleLink, badges, reason),
  );
}

function renderPool() {
  const allFavs = loadFavs();
  const list = $("#pool-list");
  list.innerHTML = "";

  const filtered = filterFavs(allFavs);
  $("#pool-stat").textContent = allFavs.length === 0
    ? "暂无收藏"
    : `共 ${allFavs.length} 条收藏 · 当前显示 ${filtered.length} 条`;

  if (allFavs.length === 0) {
    list.appendChild(el("div", { class: "empty" },
      el("span", {}, "还没有收藏。去 "),
      el("a", { href: "index.html" }, "主工作台"),
      el("span", {}, " 点几个 👍 再回来。")));
    return;
  }

  if (filtered.length === 0) {
    list.appendChild(el("div", { class: "empty" }, "当前条件下没有匹配项"));
    return;
  }

  // 清理：勾选里如果有不在当前 favs 的（fav 被删了），从 selected 里移除
  const validIds = new Set(allFavs.map(f => f.id));
  for (const id of [...state.selectedIds]) {
    if (!validIds.has(id)) state.selectedIds.delete(id);
  }

  filtered.forEach(fav => list.appendChild(renderPoolItem(fav)));
}

// ============================================================
// 右侧控制面板
// ============================================================

function renderSelectedPanel() {
  const allFavs = loadFavs();
  const idSet = state.selectedIds;
  const sel = allFavs.filter(f => idSet.has(f.id));

  $("#selected-count").textContent = `已选 ${sel.length} 条`;

  const preview = $("#selected-preview");
  preview.innerHTML = "";

  if (sel.length === 0) {
    preview.appendChild(el("div", { class: "muted" }, "从左侧勾选 2-12 条素材..."));
    return;
  }

  sel.forEach(f => {
    const titleCn = normalizeTitleCn(f);
    const removeBtn = el("button", {
      class: "wb-selected-x",
      title: "从勾选中移除",
      onclick: () => {
        state.selectedIds.delete(f.id);
        renderSelectedPanel();
        // 同步取消 checkbox
        const cb = document.querySelector(`.wb-pool-check[data-id="${f.id}"]`);
        if (cb) cb.checked = false;
        const item = document.querySelector(`.wb-pool-item[data-id="${f.id}"]`);
        if (item) item.classList.remove("selected");
        refreshGenerateBtn();
      },
    }, "✕");

    preview.appendChild(el("div", { class: "wb-selected-item" },
      el("span", { class: "wb-selected-title" }, titleCn),
      removeBtn,
    ));
  });
}

function refreshGenerateBtn() {
  const btn = $("#generate-btn");
  const n = state.selectedIds.size;
  if (state.generating) {
    btn.disabled = true;
    btn.textContent = "⏳ 生成中...";
    return;
  }
  if (n < 2) {
    btn.disabled = true;
    btn.textContent = `✨ 生成洞察（已选 ${n} 条，至少 2 条）`;
  } else if (n > 12) {
    btn.disabled = true;
    btn.textContent = `❌ 超过上限（已选 ${n}，最多 12）`;
  } else {
    btn.disabled = false;
    btn.textContent = `✨ 生成洞察（${n} 条）`;
  }
}

// ============================================================
// 生成
// ============================================================

function getSelectedModel() {
  const r = document.querySelector('input[name="model"]:checked');
  return (r && r.value) || "reasoner";
}

async function doGenerate() {
  if (state.generating) return;

  const allFavs = loadFavs();
  const selected = allFavs.filter(f => state.selectedIds.has(f.id));
  if (selected.length < 2) return;

  const userNote = ($("#user-note").value || "").trim();
  const model = getSelectedModel();

  // 把 favorites 里的字段裁剪到 generate-tip 接受的形态
  const articlesPayload = selected.map(f => ({
    id: f.id,
    title: f.title || "",
    url: f.url || "",
    summary: f.summary || "",
    source_name: f.source_name || "",
    source_tier: f.source_tier || "C",
    content_type: f.content_type || "opinion_analysis",
    published_at: f.published_at || "",
    total_score: f.total_score || 0,
  }));

  state.generating = true;
  refreshGenerateBtn();

  const status = $("#generate-status");
  const startedAt = Date.now();
  const expectedSec = model === "reasoner" ? "约 15-25 秒" : "约 8-12 秒";
  status.textContent = `⏳ 正在生成（${expectedSec}）—— 调 ${selected.length} 次搜索 + 1 次 LLM`;

  // 进度提示（每秒更新一次"已用 Xs"）
  let timer = setInterval(() => {
    const sec = Math.round((Date.now() - startedAt) / 1000);
    status.textContent = `⏳ 已耗时 ${sec}s（预期 ${expectedSec}）`;
  }, 1000);

  try {
    const headers = { "Content-Type": "application/json" };
    if (TIPS_SECRET) headers["X-Tips-Secret"] = TIPS_SECRET;

    const res = await fetch(GENERATE_ENDPOINT, {
      method: "POST",
      headers,
      body: JSON.stringify({
        articles: articlesPayload,
        userNote,
        model,
        doSearch: true,
      }),
    });

    clearInterval(timer);

    const data = await res.json();
    if (!res.ok || !data.ok) {
      throw new Error(data.error || `HTTP ${res.status}`);
    }

    state.currentResult = {
      tip: data.tip,
      model: data.model,
      modelChoice: data.modelChoice || model,
      elapsedMs: data.elapsedMs,
      llmElapsedMs: data.llmElapsedMs,
      searchLog: data.searchLog || [],
      nArticles: data.nArticles,
      nSearches: data.nSearches,
      userNoteUsed: data.userNoteUsed,
      promptChars: data.promptChars,
      outputChars: data.outputChars,
      costEstimateCny: data.costEstimateCny || 0,
      generatedAt: new Date().toISOString(),
      // 保留完整选材，便于历史回看
      articles: articlesPayload.map(a => ({
        id: a.id,
        title: a.title,
        url: a.url,
        source_name: a.source_name,
      })),
      userNote,
    };

    status.innerHTML = `✅ 生成成功（${(data.elapsedMs/1000).toFixed(1)}s · ${data.outputChars} 字 · 估算成本 ¥${(data.costEstimateCny || 0).toFixed(4)}）`;
    renderCurrentResult();
  } catch (err) {
    clearInterval(timer);
    status.innerHTML = `❌ 生成失败：${(err && err.message) || err}`;
    state.currentResult = null;
    $("#result-section").style.display = "none";
  } finally {
    state.generating = false;
    refreshGenerateBtn();
  }
}

// ============================================================
// 当前结果
// ============================================================

// v1.8：跟 v2/app.js 的主题洞察渲染保持完全一致
//   - 安全解析 **xxx** 为 <strong class="topic-insight-bold">
//   - 段落分隔（\n\n）→ <p class="topic-insight-paragraph">
//   - 段内换行（\n）→ <br>
//   - 全程走 textNode + DOM API，无 innerHTML，无 XSS
function renderTipWithBold(host, raw) {
  if (!raw) return;
  const text = String(raw).replace(/\r\n/g, "\n").trim();
  const paragraphs = text.split(/\n{2,}/);
  paragraphs.forEach(para => {
    const p = el("p", { class: "topic-insight-paragraph" });
    const regex = /\*\*([^*\n]+?)\*\*/g;
    let lastIndex = 0;
    let m;
    while ((m = regex.exec(para)) !== null) {
      appendTextWithBr(p, para.slice(lastIndex, m.index));
      p.appendChild(el("strong", { class: "topic-insight-bold" }, m[1]));
      lastIndex = m.index + m[0].length;
    }
    appendTextWithBr(p, para.slice(lastIndex));
    host.appendChild(p);
  });
}

function appendTextWithBr(host, str) {
  if (!str) return;
  const lines = str.split("\n");
  lines.forEach((line, i) => {
    if (i > 0) host.appendChild(el("br"));
    if (line) host.appendChild(document.createTextNode(line));
  });
}

function renderCurrentResult() {
  const r = state.currentResult;
  if (!r) {
    $("#result-section").style.display = "none";
    return;
  }
  $("#result-section").style.display = "";
  $("#result-meta").innerHTML =
    `模型：<b>${r.model}</b> · 耗时 ${(r.elapsedMs/1000).toFixed(1)}s · ` +
    `搜索 ${r.nSearches}/${r.nArticles} · ${r.outputChars} 字 · ` +
    `估算 ¥${(r.costEstimateCny || 0).toFixed(4)}`;

  // 用加粗渲染替代 textContent，保持与主题洞察一致的视觉
  const body = $("#result-body");
  body.innerHTML = "";  // 清空但不留 HTML 注入隐患（后面用 textNode）
  renderTipWithBold(body, r.tip);

  // 滚到结果
  $("#result-section").scrollIntoView({ behavior: "smooth", block: "start" });
}

function copyResult() {
  const r = state.currentResult;
  if (!r) return;
  navigator.clipboard.writeText(r.tip).then(
    () => { $("#generate-status").innerHTML = "📋 已复制到剪贴板"; },
    (e) => { alert("复制失败：" + e.message); },
  );
}

function saveResultToHistory() {
  const r = state.currentResult;
  if (!r) return;
  const hist = loadHistory();
  // 给一个 ID 防止重复保存
  const entry = {
    id: `tip_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    ...r,
  };
  hist.unshift(entry);
  // 上限 100 条
  if (hist.length > 100) hist.length = 100;
  saveHistory(hist);
  state.currentResult = null;
  $("#result-section").style.display = "none";
  $("#generate-status").innerHTML = "💾 已保存到历史";
  renderHistory();
}

function discardResult() {
  if (!state.currentResult) return;
  if (!confirm("确定弃用这条？将不可恢复。")) return;
  state.currentResult = null;
  $("#result-section").style.display = "none";
  $("#generate-status").innerHTML = "🗑️ 已弃用";
}

// ============================================================
// 历史
// ============================================================

function renderHistory() {
  const hist = loadHistory();
  const list = $("#history-list");
  list.innerHTML = "";
  $("#history-stat").textContent = hist.length === 0 ? "暂无历史" : `共 ${hist.length} 条`;

  if (hist.length === 0) {
    list.appendChild(el("div", { class: "empty" }, "还没有保存的 tips。生成一条后点 💾 保存即可"));
    return;
  }

  hist.forEach(entry => {
    const meta = el("div", { class: "wb-history-meta muted" },
      `${fmtDateTime(entry.generatedAt)} · ${entry.model} · ` +
      `${entry.nArticles} 条素材 · ${entry.outputChars} 字 · ` +
      `¥${(entry.costEstimateCny || 0).toFixed(4)}`,
    );

    const sources = el("div", { class: "wb-history-sources" },
      ...(entry.articles || []).map(a =>
        el("span", { class: "wb-history-source" },
          el("a", { href: a.url, target: "_blank", rel: "noopener noreferrer" },
            normalizeTitleCn(a)))));

    // 历史正文也用加粗渲染保持一致
    const histBody = el("div", { class: "wb-history-text" });
    renderTipWithBold(histBody, entry.tip);
    const bodyToggle = el("details", { class: "wb-history-body" },
      el("summary", {}, "查看正文"),
      histBody);

    const noteBlock = entry.userNote ? el("details", { class: "wb-history-note" },
      el("summary", { class: "muted" }, "💭 当时的补充"),
      el("div", { class: "wb-history-text muted" }, entry.userNote)) : null;

    const copyBtn = el("button", {
      class: "wb-secondary-btn",
      onclick: () => navigator.clipboard.writeText(entry.tip).then(
        () => alert("已复制"), () => alert("复制失败")),
    }, "📋 复制");
    const removeBtn = el("button", {
      class: "wb-danger-btn",
      onclick: () => {
        if (!confirm("从历史中删除这条？不可恢复。")) return;
        const next = loadHistory().filter(x => x.id !== entry.id);
        saveHistory(next);
        renderHistory();
      },
    }, "🗑️");

    list.appendChild(el("article", { class: "wb-history-card" },
      meta, sources, bodyToggle, noteBlock,
      el("div", { class: "wb-history-actions" }, copyBtn, removeBtn)));
  });
}

// ============================================================
// 事件绑定
// ============================================================

function bind() {
  $("#time-filter").addEventListener("change", (e) => {
    state.timeFilter = e.target.value;
    renderPool();
  });
  $("#title-search").addEventListener("input", (e) => {
    state.searchKeyword = e.target.value;
    renderPool();
  });
  $("#select-none-btn").addEventListener("click", () => {
    state.selectedIds.clear();
    renderPool();
    renderSelectedPanel();
    refreshGenerateBtn();
  });
  $("#generate-btn").addEventListener("click", doGenerate);
  $("#copy-btn").addEventListener("click", copyResult);
  $("#save-btn").addEventListener("click", saveResultToHistory);
  $("#discard-btn").addEventListener("click", discardResult);
}

// ============================================================
// 启动
// ============================================================

renderPool();
renderSelectedPanel();
refreshGenerateBtn();
renderHistory();
bind();
