// Vercel Serverless Function: /api/tavily-search
//
// 作用：把 Tavily Search API 包一层中转。
//   1. 解决国内 Mac / GitHub Actions 直连 api.tavily.com 被防火墙拦的问题
//      （Vercel 边缘节点在海外，Vercel → Tavily 直连无障碍）
//   2. Tavily API key 只放在 Vercel 环境变量，不暴露给浏览器/客户端
//   3. 共享 secret 鉴权防止任意人调用消耗你的 Tavily 配额
//
// 环境变量（在 Vercel Dashboard → Settings → Environment Variables 配置）：
//   TAVILY_API_KEY     —— 从 https://tavily.com/ Dashboard 拿到的 tvly-xxx key
//   FAV_SHARED_SECRET  —— 复用 fav 接口的同一个 secret（也可以单独定义 TIPS_SHARED_SECRET，留空则跟 FAV）
//   TIPS_SHARED_SECRET —— 可选；优先级高于 FAV_SHARED_SECRET
//
// 客户端调用示例（POST）：
//   fetch('/api/tavily-search', {
//     method: 'POST',
//     headers: { 'Content-Type': 'application/json', 'X-Tips-Secret': '<同环境变量>' },
//     body: JSON.stringify({
//       query: 'California AG sues Chrome Holding genetic data 2026',
//       max_results: 5,
//       search_depth: 'basic',           // 'basic' | 'advanced'
//       include_answer: true,
//       include_raw_content: false,
//       include_domains: [],             // 可选白名单
//       exclude_domains: [],             // 可选黑名单
//     })
//   })
//
// GET：返回一个简单的 health check（用于部署后快速验证 endpoint 已上线）。

const TAVILY_API_KEY = process.env.TAVILY_API_KEY;
const TIPS_SECRET = process.env.TIPS_SHARED_SECRET || process.env.FAV_SHARED_SECRET || '';

const TAVILY_URL = 'https://api.tavily.com/search';

function j(res, status, body) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-Tips-Secret, X-Fav-Secret');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.end(JSON.stringify(body));
}

export default async function handler(req, res) {
  try {
    // CORS 预检
    if (req.method === 'OPTIONS') return j(res, 200, { ok: true });

    // GET：health check
    if (req.method === 'GET') {
      return j(res, 200, {
        ok: true,
        endpoint: '/api/tavily-search',
        method: 'POST',
        hasKey: !!TAVILY_API_KEY,
        hasSecret: !!TIPS_SECRET,
      });
    }

    if (req.method !== 'POST') {
      return j(res, 405, { error: 'method not allowed' });
    }

    if (!TAVILY_API_KEY) {
      return j(res, 500, { error: 'missing env var TAVILY_API_KEY' });
    }

    // 鉴权（X-Tips-Secret 优先；body.secret 兜底；header 还接受 X-Fav-Secret 复用同一份 secret）
    const secret =
      req.headers['x-tips-secret'] ||
      req.headers['x-fav-secret'] ||
      (req.body && req.body.secret);
    if (TIPS_SECRET && secret !== TIPS_SECRET) {
      return j(res, 403, { error: 'forbidden (shared secret mismatch)' });
    }

    const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});
    const query = (body.query || '').trim();
    if (!query) return j(res, 400, { error: 'missing query' });

    // 透传到 Tavily 的参数（白名单，避免客户端塞奇怪字段）
    const payload = {
      api_key: TAVILY_API_KEY,
      query,
      search_depth: body.search_depth === 'advanced' ? 'advanced' : 'basic',
      max_results: Math.min(Math.max(parseInt(body.max_results, 10) || 5, 1), 20),
      include_answer: body.include_answer !== false,            // 默认 true
      include_raw_content: body.include_raw_content === true,   // 默认 false（raw 太大）
    };
    if (Array.isArray(body.include_domains) && body.include_domains.length) {
      payload.include_domains = body.include_domains.slice(0, 50);
    }
    if (Array.isArray(body.exclude_domains) && body.exclude_domains.length) {
      payload.exclude_domains = body.exclude_domains.slice(0, 50);
    }
    if (body.days != null) {
      const d = parseInt(body.days, 10);
      if (d > 0 && d <= 365) payload.days = d;
    }
    if (body.topic === 'news' || body.topic === 'general') {
      payload.topic = body.topic;
    }

    const t0 = Date.now();
    const r = await fetch(TAVILY_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'User-Agent': 'briefing-tavily-proxy/0.1',
      },
      body: JSON.stringify(payload),
    });
    const elapsedMs = Date.now() - t0;

    const text = await r.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      return j(res, 502, {
        error: 'tavily returned non-json',
        upstream_status: r.status,
        upstream_body_preview: text.slice(0, 500),
        elapsed_ms: elapsedMs,
      });
    }

    if (!r.ok) {
      return j(res, r.status, {
        error: 'tavily upstream error',
        upstream_status: r.status,
        upstream_body: data,
        elapsed_ms: elapsedMs,
      });
    }

    // 透传成功结果，附加 elapsed_ms 便于客户端观测
    return j(res, 200, {
      ok: true,
      elapsed_ms: elapsedMs,
      ...data,
    });
  } catch (err) {
    console.error('[/api/tavily-search] error:', err);
    return j(res, 500, { error: String(err.message || err) });
  }
}
