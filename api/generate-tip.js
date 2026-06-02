// Vercel Serverless Function: /api/generate-tip
//
// 作用：工作台核心 LLM 生成 endpoint。
//   1. 接受前端 POST：{ articles: [...], userNote: "...", model: "reasoner"|"chat" }
//   2. 对每条 article 调 /api/tavily-search 拿原文/公开报道（与用户敲定的"全部都搜"策略一致）
//   3. 注入用户范文笔法（从 git 仓库读 brain/style_samples 的镜像副本）
//   4. 调 DeepSeek (reasoner 或 chat) 生成 300-500 字洞察
//   5. 返回 { tip, model, elapsedMs, searchLog, costEstimateCny }
//
// 环境变量（在 Vercel Dashboard 配置）：
//   LLM_API_KEY        DeepSeek API key
//   TIPS_SHARED_SECRET 鉴权 secret（前端 X-Tips-Secret 必须匹配）
//   FAV_SHARED_SECRET  备选鉴权（兼容现有客户端）
//   TAVILY_API_KEY     Tavily key（仅由 /api/tavily-search 使用，本 function 通过 fetch 内部 url 复用）
//
// 注意：
//   - Hobby plan 默认 maxDuration=60s。reasoner 实测 12-15s + 4 次 Tavily 各 2-3s ≈ 25-30s 总耗时
//   - 前端需要展示"⏳ 生成中（最多 30 秒）"loading 提示
//   - 失败时返回 5xx + 错误信息，前端展示

export const config = {
  maxDuration: 60,
};

const LLM_API_KEY = process.env.LLM_API_KEY;
const LLM_BASE_URL = (process.env.LLM_BASE_URL || 'https://api.deepseek.com/v1').replace(/\/$/, '');
const TIPS_SECRET = process.env.TIPS_SHARED_SECRET || process.env.FAV_SHARED_SECRET || '';

// 内部调用 /api/tavily-search 时不需要带 secret（Vercel 内部网络），
// 但为了对称性还是带上
const VERCEL_URL = process.env.VERCEL_URL ? `https://${process.env.VERCEL_URL}` : '';

// ⚠️ 2026-06-02 紧急止血：reasoner 选项已禁用
// 原因：6/2 单天 deepseek-v4-pro 烧了约 ¥27（3529 次调用，4.27 亿 token），
//       即使设了 max_tokens=3000，DeepSeek reasoner 的 reasoning_content（思考链）
//       在某些情况下绕过 max_tokens 限制，单次仍可能产生 5-10 万 token。
//       且公开 endpoint + 前端硬编码 secret 让任何能 view source 的人都能刷。
//
// 修复方案（v1.7）：
//   1. 服务端把 model="reasoner" 强制 fallback 到 chat（用户即使在前端选 reasoner，
//      实际用的也是 chat）
//   2. 单 IP 速率限制（5 分钟内最多 3 次）防扫
//   3. 之后再考虑：把 reasoner 换成 chat-v3.2（同档位非推理模型）做"深度模式"
const MODEL_SPECS = {
  reasoner: {
    // ⚠️ 实际仍走 chat 模型（紧急止血），保留 key 兼容前端传入
    model: 'deepseek-chat',
    maxTokens: 2000,
    pricePer1MOutputCny: 2,
    pricePer1MInputCny: 1,
    note: 'fallback-to-chat (reasoner disabled 2026-06-02 due to runaway cost)',
  },
  chat: {
    model: 'deepseek-chat',
    maxTokens: 1500,
    pricePer1MOutputCny: 2,
    pricePer1MInputCny: 1,
  },
};

// ---- 极简内存速率限制（防恶意刷）----
// Vercel function 跨调用不保证状态，但同一容器内能限流；多容器最坏情况是限流被绕过
// （每容器 N 次），但已经把 reasoner 关了所以最坏也只是浪费 chat 的钱（¥0.005/次）
const _rateLimitMap = new Map();  // ip -> [timestamp, ...]
const RATE_LIMIT_WINDOW_MS = 5 * 60 * 1000;  // 5 min
const RATE_LIMIT_MAX = 5;                     // 每 IP 每 5 min 最多 5 次

function checkRateLimit(ip) {
  const now = Date.now();
  const arr = (_rateLimitMap.get(ip) || []).filter(t => now - t < RATE_LIMIT_WINDOW_MS);
  if (arr.length >= RATE_LIMIT_MAX) return false;
  arr.push(now);
  _rateLimitMap.set(ip, arr);
  // 清理：只保留最近 50 个 IP，防内存膨胀
  if (_rateLimitMap.size > 50) {
    const oldestKey = _rateLimitMap.keys().next().value;
    _rateLimitMap.delete(oldestKey);
  }
  return true;
}

// ============================================================
// 嵌入式范文笔法（从 brain/style_samples 抽离的核心，避免函数读外部文件）
// ============================================================
// 这里同步 app/tips_generator.py 的 INSIGHT_INSTRUCTION——保持两端一致
const INSIGHT_INSTRUCTION = `请直接产出一段 300-500 字的「深度洞察」，作为下一期《网络治理动态速递》的核心评论段。

## 用户的真实需求（必读）

1. **用户已经知道每条事实在讲什么**——他自己勾选的素材，对每条的来龙去脉都心里有数
2. **不要复述事实**——每条事实最多用半句话作为论据带出（例："西班牙近期推出转化立法"），
   不要出现"2026 年 X 月 X 日，西班牙政府批准了……"这种完整事实陈述
3. **要的是炒一盘菜，不是食材摆盘**——
   不要按"事实 1 → 事实 2 → 事实 3"逐条分析，
   也不要"事实层 / 观点层 / 问题层"三栏罗列，
   要的是把这些事实揉碎，提取出一条主线判断，再围绕这条主线展开论证

## 洞察的两种角度（请混合使用，不要单一）

- **网信办视角**：这些动态对中国监管者意味着什么——哪些是中国可借鉴或警示的，
  哪些暴露了海外治理的盲区，哪些在中国已经先行/落后
- **行业全景视角**：把这一批事实放在更大的时空坐标里——
  全球 AI 治理在这个时点呈现什么宏观趋势，欧美各自走到哪一步，
  老问题（数据主体权益、跨境流动、敏感数据保护）如何被新技术/新场景重新激活

## 文风约束（严格遵守用户范文笔法）

1. **第一句**：抛出本批事实背后的共同主线判断，但不要写成
   "近期围绕 X 的若干动态，呈现出一条共同的脉络"这种 AI 套话开头。
   范文示例可参考："从算法到模型，人工智能正跨越一个分水岭"
   或"开源之所以宝贵，是因为其低门槛、高透明度带来了源源不断的创意和改进"。
   要给出有信息量的判断句，不是空泛过渡句

2. **段落结构**：1-2 段，每段 200-400 字，论证型长段，不是金句型短段
   - 先抛"判断"
   - 再用"机制 + 例证"展开（事实当例证，一句话点过）
   - 用 **一是 / 二是 / 三是** 或 **其一 / 其次 / 最后** 串联展开
   - **重要**：每个 "一是/二是/三是" **必须独立另起一行**（前后各空一行也行），
     这是屏幕扫读的关键——读者的眼睛要能在 1 秒内定位到几条并列论点
   - 序号词本身可以加粗（详见第 4 条）

3. **数字、引语**：自然嵌入论述，不单独成行
   （例："超过85万加州居民的基因数据被泄露"嵌进句中作为论据）

4. **加粗规则**（用 Markdown **xxx** 语法；前端会渲染成粗体 + 极淡朱红底色）
   - 全文加粗 **6-12 处**；目的是让读者扫一眼就能抓到核心论点
   - **加粗可以是整句**——不是只标关键词，而是把"这一句的判断本身"
     整句圈起来，让读者一眼看到一句完整论点
   - **必须加粗**以下五类：
     a. **每段开篇的核心判断整句**（一整句最多 25-40 字。
        例：**"从算法到模型，人工智能监管正在跨越一个分水岭"** 整句加粗；
        例：**"欧盟 AI 法案正在塑造全球 AI 治理的'欧洲样本'"** 整句加粗）。
        全文 1-2 段，因此整段开篇判断句加粗 1-2 次
     b. **每个"一是/二是/三是"后面的核心论点整句**——
        不是只加粗"一是"两个字，而是把"一是 + 这一条论点"整句圈起来。
        例：**"一是，欧洲正以'先定框架、再补细则'的策略推进"** 整句加粗；
        例：**"二是，对中国监管者而言，欧洲的体系化与美国的动态调整都提供了参照"**
        整句加粗。每段并列 2-3 条，每条加粗 1 句，共 2-4 处
     c. **关键术语首次出现**（**数据保护影响评估**、**显著风险阈值**、
        **避风港规则**、**风险分级** 等专业术语）——加粗 1-3 处
     d. **关键数字事实**（**超过 85 万加州居民**、**违法所得 5 倍**、
        **逾 200 家平台**）——加粗 1-2 处
     e. **结尾段的概括判断整句**（如果有的话）——可加粗 0-1 句
   - **严禁加粗**：
     × 章节标签（~~**核心观点：**~~、~~**第一、**~~、~~**核心结论：**~~）——
       这是 ChatGPT prompt 痕迹，会立刻暴露 AI 写作
     × 连接词（~~**事实上**~~、~~**整体来看**~~、~~**值得注意的是**~~）
     × 一整段（4 句以上）从头加粗到尾——会变成"灰墙变彩墙"
   - **节奏原则**：每段 200-400 字里，加粗句最多 2-3 句，
     非加粗的论证细节句要占大多数；加粗是"路标"不是"内容"

5. **结尾**：以一段平和观察或一句自然引语收住，**不下硬结论**，
   不写"真正的考验从 X 才开始"或"喘息不是和解"这种金句留白

## 严格禁用（命中即不合格）

- 复述事实（"2026 年 5 月 26 日..."）
- "近期围绕 X 的若干动态，呈现出一条共同的脉络"这种 AI 套话开头
- "更值得关注的是" / "归根结底" / "读懂了这层" / "换言之" / "不可忽视的是"
- 金句留白结尾
- "## 一、背景 / ## 二、内容" 三段式骨架
- emoji ✅❌ 符号
- 画面感开篇（"X 月 X 日傍晚"）
- "对 X 而言" 连用三段做"影响分析"

## 产出

直接输出洞察段落，不要前言后语，不要标题，不要"以下是我的洞察"这种过渡句。`;

// 用户文风的精炼版（节选 brain/style_samples/00_文风提炼.md 的关键约束）
// 完整版保留在 Python 端 app/tips_generator.py（CLI 跑时用），
// 这里嵌入精简版避免在 serverless function 里读外部文件
const STYLE_SUMMARY = `# 用户文风提炼（务必遵守）

## 一、最关键的"AI 味"识别清单

| AI 味症状 | 真实文风 |
|---|---|
| 用 "第一/第二/第三" 起句 | 用 "一是/二是/三是"，且常嵌在长句之中 |
| "本节将分析……" 类元叙述 | 直接进入论述 |
| "更值得关注的是" / "归根结底" 高频出现 | 真实文风很少用，更多是 "事实上""特别是""相较于""更进一步""也因此" |
| 加粗"判断句"作为视觉锚点 | 真实文风很少加粗，至多加粗少数关键术语首次出现，全文加粗不超过 3-5 处 |
| 表情/分隔符过多、emoji、✅/❌ | 完全不用 |
| 短句节奏明显（"喘息不是和解"这种警句式）| 真实文风长句为主，论证型，不追求金句 |
| 标题套路："XX 的 X 条主线 / X 个维度 / X 大启示" | 真实标题质朴 |
| 引言用"2026 年 5 月 19 日傍晚" 营造画面感 | 真实文风开头平实，先抛核心观点+背景概述 |
| "对 X 而言"格式连用三段制造影响分析 | 真实文风常以"整体看""总体看""综合而言"收尾 |

## 二、真实文风的特征

### 1. 段落结构：先抛"判断"，再用"机制 + 例证"展开

每段开头是一个明确判断句（不需要加粗，靠句意自然立住），后面跟"为什么这样"，再跟"具体看"。
用 "一方面/另一方面"、"一是/二是"、"其一/其次/最后" 串联，**不分行不加粗**，连成一段散文式论述。

### 2. 引用与论据：自然嵌入，不做"事实+置信度"式罗列

引语直接嵌进句子，作者用一句"有专家指出""相关智库曾批评"自然带出，不做结构化呈现。

### 3. 数字使用：克制，且服务论证

数字总是为论点服务，不堆砌。

### 4. 比喻与类比：朴素而精准

不用"裁决书""分水岭""涟漪"这种文学化喻体（除非原文献里就有）。

### 5. 收尾方式：观察 + 期许，不强行金句

第一篇收在引语：只有通过技术扩散，才能真正识别其效用或缺陷
第二篇收在期许：加强信息素养教育...
第三篇收在画面：达成开放协作敏捷高效的安全协作

**没有"喘息不是和解，真正的考验从 2027 才开始"这种短句式留白**。真实结尾平和、内敛、有余味。`;

// ============================================================
// 工具
// ============================================================

function j(res, status, body) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-Tips-Secret, X-Fav-Secret');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.end(JSON.stringify(body));
}

const ZERO_WIDTH = /[\u200b-\u200f\u2060-\u206f\u00ad\u034f\u180e]/g;

function cleanSummary(text) {
  if (!text) return '';
  return String(text).replace(ZERO_WIDTH, '').replace(/\s+/g, ' ').trim();
}

function extractQueryForTavily(article) {
  const title = (article.title || '').trim();
  const summary = cleanSummary(article.summary || '');
  const head = summary.slice(0, 150);
  const query = head ? `${title} ${head}` : title;
  return query.slice(0, 380);
}

// 调内部 /api/tavily-search
async function callTavilySearch(query, host) {
  // host = req.headers.host（包含端口和域名），用它构造同源 URL
  const proto = host && host.includes('localhost') ? 'http' : 'https';
  const url = `${proto}://${host}/api/tavily-search`;
  const headers = { 'Content-Type': 'application/json' };
  if (TIPS_SECRET) headers['X-Tips-Secret'] = TIPS_SECRET;

  const payload = {
    query,
    search_depth: 'basic',
    max_results: 5,
    include_answer: true,
    include_raw_content: false,
  };

  const t0 = Date.now();
  const r = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  });
  const elapsed = Date.now() - t0;
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`tavily ${r.status}: ${text.slice(0, 200)}`);
  }
  const data = await r.json();
  return { data, elapsedMs: elapsed };
}

// 调 DeepSeek
async function callLlm(system, user, modelSpec) {
  const url = `${LLM_BASE_URL}/chat/completions`;
  const payload = {
    model: modelSpec.model,
    messages: [
      { role: 'system', content: system },
      { role: 'user', content: user },
    ],
    temperature: 0.6,
    max_tokens: modelSpec.maxTokens,
  };
  const t0 = Date.now();
  const r = await fetch(url, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${LLM_API_KEY}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });
  const elapsed = Date.now() - t0;
  const text = await r.text();
  if (!r.ok) {
    throw new Error(`llm ${r.status}: ${text.slice(0, 300)}`);
  }
  let obj;
  try {
    obj = JSON.parse(text);
  } catch {
    throw new Error('llm response not json');
  }
  const content = obj.choices?.[0]?.message?.content || '';
  if (!content) throw new Error('llm empty content');
  return { content, usage: obj.usage || {}, elapsedMs: elapsed };
}

function estimateCostCny(usage, modelSpec) {
  if (!usage) return 0;
  const promptTok = usage.prompt_tokens || 0;
  const completionTok = usage.completion_tokens || 0;
  // DeepSeek 给了细分字段就用细分（cache_hit/miss）
  const cacheHit = usage.prompt_cache_hit_tokens || 0;
  const cacheMiss = usage.prompt_cache_miss_tokens || (cacheHit > 0 ? Math.max(0, promptTok - cacheHit) : promptTok);

  // 按 modelSpec 单价（这里简化：cache_hit 按 input/2，cache_miss 按 input）
  const inputCost = (cacheMiss * modelSpec.pricePer1MInputCny + cacheHit * modelSpec.pricePer1MInputCny / 2) / 1_000_000;
  const outputCost = (completionTok * modelSpec.pricePer1MOutputCny) / 1_000_000;
  return Math.round((inputCost + outputCost) * 10000) / 10000;  // 4 位小数
}

// ============================================================
// 拼 prompt
// ============================================================

function buildPrompts(articles, enrichments, userNote) {
  const system =
    `你是腾讯研究院「大模型研究小分队」的资深写手。\n` +
    `你正在为《网络治理动态速递》写一段核心评论——给中央网信办相关研究人员看。\n` +
    `只关注域外（海外）网络治理动态。\n\n` +
    STYLE_SUMMARY;

  const articleBlocks = articles.map((a, i) => {
    const lines = [
      `### 素材 ${i + 1}`,
      `- 标题：${a.title || ''}`,
      `- 信源：${a.source_name || '?'}（${a.source_tier || 'C'} 级）`,
      `- 类型：${a.content_type || 'opinion_analysis'}`,
      `- 时间：${(a.published_at || '').slice(0, 10)}`,
    ];
    const cleanSum = cleanSummary(a.summary || '');
    if (cleanSum) lines.push(`- 摘要供参考：${cleanSum.slice(0, 500)}`);

    const enrich = enrichments[a.id];
    if (enrich && !enrich.error) {
      const answer = enrich.answer || '';
      const results = enrich.results || [];
      if (answer || results.length) {
        lines.push('- Tavily 公开信源补全：');
        if (answer) lines.push(`  - 一句话总结：${answer.slice(0, 300)}`);
        results.slice(0, 3).forEach((r, j) => {
          let host = '?';
          if (r.url && r.url.includes('://')) {
            host = r.url.split('/')[2];
          }
          const content = (r.content || '').slice(0, 300);
          lines.push(`  - 来源 ${j + 1} [${host}]：${content}`);
        });
      }
    }
    return lines.join('\n');
  });

  const userParts = [
    `## 用户已勾选的 ${articles.length} 条素材（用户自己看过，不需要复述）`,
    '',
    articleBlocks.join('\n\n'),
  ];

  if (userNote && userNote.trim()) {
    userParts.push('', '## 用户的额外补充（用户提供的判断主线，请围绕此展开）', '', userNote.trim());
  } else {
    userParts.push('', '## 用户没有提供额外补充', '', '请你自己从这些素材里提取一条主线判断，写出深度洞察。');
  }

  userParts.push('', '## 现在请你输出', '', INSIGHT_INSTRUCTION);

  return { system, user: userParts.join('\n') };
}

// ============================================================
// 主 handler
// ============================================================

export default async function handler(req, res) {
  try {
    if (req.method === 'OPTIONS') return j(res, 200, { ok: true });

    if (req.method === 'GET') {
      // health check
      return j(res, 200, {
        ok: true,
        endpoint: '/api/generate-tip',
        method: 'POST',
        hasLlmKey: !!LLM_API_KEY,
        hasSecret: !!TIPS_SECRET,
        models: Object.keys(MODEL_SPECS),
      });
    }

    if (req.method !== 'POST') return j(res, 405, { error: 'method not allowed' });

    if (!LLM_API_KEY) return j(res, 500, { error: 'missing env LLM_API_KEY' });

    // 鉴权
    const secret =
      req.headers['x-tips-secret'] ||
      req.headers['x-fav-secret'] ||
      (req.body && req.body.secret);
    if (TIPS_SECRET && secret !== TIPS_SECRET) {
      return j(res, 403, { error: 'forbidden (shared secret mismatch)' });
    }

    // ---- 速率限制（v1.7，2026-06-02 防扫）----
    const clientIp = (req.headers['x-forwarded-for'] || '').split(',')[0].trim()
      || req.headers['x-real-ip']
      || 'unknown';
    if (!checkRateLimit(clientIp)) {
      return j(res, 429, {
        error: 'rate limit exceeded',
        detail: `每 5 分钟最多 ${RATE_LIMIT_MAX} 次请求；请稍后再试`,
      });
    }

    const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});

    const articles = Array.isArray(body.articles) ? body.articles : [];
    if (articles.length === 0) return j(res, 400, { error: 'no articles selected' });
    if (articles.length > 12) return j(res, 400, { error: 'too many articles (max 12)' });

    const userNote = (body.userNote || '').toString().slice(0, 2000);
    const modelChoice = body.model === 'chat' ? 'chat' : 'reasoner';
    const modelSpec = MODEL_SPECS[modelChoice];
    const doSearch = body.doSearch !== false;  // 默认 true

    const t0 = Date.now();

    // ---- 1. Tavily 搜索（每条都搜）----
    const enrichments = {};
    const searchLog = [];
    let nSearches = 0;

    if (doSearch) {
      const host = req.headers.host;
      // 串行调（避免一次并发 10 个 fetch 触发 Vercel 限制；4 条 × 2-3s ≈ 8-12s，可接受）
      for (const a of articles) {
        const query = extractQueryForTavily(a);
        const log = {
          articleId: a.id,
          title: (a.title || '').slice(0, 80),
          query: query.slice(0, 200),
          ok: false,
          nResults: 0,
          elapsedMs: 0,
        };
        try {
          const { data, elapsedMs } = await callTavilySearch(query, host);
          log.ok = true;
          log.nResults = (data.results || []).length;
          log.elapsedMs = elapsedMs;
          enrichments[a.id] = data;
          nSearches += 1;
        } catch (e) {
          log.error = String(e.message || e).slice(0, 200);
          enrichments[a.id] = { error: String(e), results: [], answer: '' };
        }
        searchLog.push(log);
      }
    }

    // ---- 2. 拼 prompt + 调 LLM ----
    const { system, user } = buildPrompts(articles, enrichments, userNote);

    let llmResult;
    try {
      llmResult = await callLlm(system, user, modelSpec);
    } catch (e) {
      return j(res, 502, {
        error: String(e.message || e).slice(0, 500),
        elapsedMs: Date.now() - t0,
        searchLog,
      });
    }

    const costCny = estimateCostCny(llmResult.usage, modelSpec);
    const elapsedMs = Date.now() - t0;

    return j(res, 200, {
      ok: true,
      tip: llmResult.content,
      model: modelSpec.model,
      modelChoice,
      elapsedMs,
      llmElapsedMs: llmResult.elapsedMs,
      searchLog,
      nArticles: articles.length,
      nSearches,
      userNoteUsed: !!(userNote && userNote.trim()),
      promptChars: system.length + user.length,
      outputChars: llmResult.content.length,
      usage: llmResult.usage,
      costEstimateCny: costCny,
    });
  } catch (err) {
    console.error('[/api/generate-tip] error:', err);
    return j(res, 500, { error: String(err.message || err).slice(0, 500) });
  }
}
