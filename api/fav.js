// Vercel Serverless Function: /api/fav
//
// 作用：前端把 localStorage 里的收藏同步到仓库 data/user_favorites.json
//
// 环境变量（在 Vercel Dashboard → Settings → Environment Variables 配置）：
//   GITHUB_TOKEN       —— 一个 PAT，只需 "Contents: Read/Write" 权限，Fine-grained 的那种
//   GITHUB_REPO        —— "SUNXIAOMEI-YUFEI/network-governance-briefing"
//   GITHUB_BRANCH      —— "main"
//   FAV_FILE_PATH      —— "data/user_favorites.json"
//   FAV_SHARED_SECRET  —— 随便一串字符串（前端要带同样的值），防止别人乱调 API
//
// 前端调用示例（POST）：
//   fetch('/api/fav', {
//     method: 'POST',
//     headers: {'Content-Type': 'application/json', 'X-Fav-Secret': '<同环境变量>'},
//     body: JSON.stringify({ favorites: [{id, url, title_cn, savedAt, ...}, ...] })
//   })
//
// GET 返回仓库里当前的 user_favorites.json（用来做跨设备初始化）

const REPO        = process.env.GITHUB_REPO;
const BRANCH      = process.env.GITHUB_BRANCH || 'main';
const FILE_PATH   = process.env.FAV_FILE_PATH || 'data/user_favorites.json';
const TOKEN       = process.env.GITHUB_TOKEN;
const SHARED_SECRET = process.env.FAV_SHARED_SECRET || '';

const GH_API = 'https://api.github.com';

function j(res, status, body) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-Fav-Secret');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.end(JSON.stringify(body));
}

async function ghGet(path) {
  const url = `${GH_API}/repos/${REPO}/contents/${path}?ref=${BRANCH}`;
  const r = await fetch(url, {
    headers: {
      'Authorization': `Bearer ${TOKEN}`,
      'Accept': 'application/vnd.github.v3+json',
      'User-Agent': 'briefing-fav-sync',
    },
  });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`GH GET ${r.status}: ${await r.text()}`);
  return r.json();
}

async function ghPut(path, content, sha, message) {
  const url = `${GH_API}/repos/${REPO}/contents/${path}`;
  const body = {
    message,
    content: Buffer.from(content, 'utf-8').toString('base64'),
    branch: BRANCH,
  };
  if (sha) body.sha = sha;

  const r = await fetch(url, {
    method: 'PUT',
    headers: {
      'Authorization': `Bearer ${TOKEN}`,
      'Accept': 'application/vnd.github.v3+json',
      'Content-Type': 'application/json',
      'User-Agent': 'briefing-fav-sync',
    },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`GH PUT ${r.status}: ${await r.text()}`);
  return r.json();
}

// 合并策略：以 savedAt（ISO 时间戳）更新的为准；按 id 去重
function mergeFavorites(oldList, newList) {
  const byId = new Map();
  for (const f of oldList || []) if (f && f.id != null) byId.set(f.id, f);
  for (const f of newList || []) {
    if (!f || f.id == null) continue;
    const exist = byId.get(f.id);
    if (!exist || (f.savedAt || '') >= (exist.savedAt || '')) {
      byId.set(f.id, f);
    }
  }
  // 按 savedAt 倒序
  return Array.from(byId.values()).sort(
    (a, b) => (b.savedAt || '').localeCompare(a.savedAt || '')
  );
}

export default async function handler(req, res) {
  try {
    // CORS 预检
    if (req.method === 'OPTIONS') return j(res, 200, { ok: true });

    if (!REPO || !TOKEN) {
      return j(res, 500, { error: 'missing env vars (GITHUB_REPO / GITHUB_TOKEN)' });
    }

    // GET：读仓库当前 favorites.json，前端用于首次初始化
    if (req.method === 'GET') {
      const meta = await ghGet(FILE_PATH);
      if (!meta) return j(res, 200, { favorites: [], sha: null });
      const content = Buffer.from(meta.content, 'base64').toString('utf-8');
      let list = [];
      try { list = JSON.parse(content) || []; } catch { list = []; }
      return j(res, 200, { favorites: list, sha: meta.sha, updatedAt: meta.commit?.committer?.date });
    }

    // POST：前端把本地 favorites 推上来合并
    if (req.method === 'POST') {
      // 简单认证：X-Fav-Secret 或 body.secret 任一符合即可
      const secret = req.headers['x-fav-secret'] || (req.body && req.body.secret);
      if (SHARED_SECRET && secret !== SHARED_SECRET) {
        return j(res, 403, { error: 'forbidden (shared secret mismatch)' });
      }

      const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});
      const incoming = Array.isArray(body.favorites) ? body.favorites : [];

      // 读仓库现有文件
      const meta = await ghGet(FILE_PATH);
      let existing = [];
      let sha = null;
      if (meta) {
        sha = meta.sha;
        try {
          existing = JSON.parse(Buffer.from(meta.content, 'base64').toString('utf-8')) || [];
        } catch { existing = []; }
      }

      const merged = mergeFavorites(existing, incoming);

      // 如果合并后和现有一致，就不写入（节省 commit 和 rate limit）
      const mergedStr = JSON.stringify(merged, null, 2);
      if (meta && mergedStr === Buffer.from(meta.content, 'base64').toString('utf-8').trim()) {
        return j(res, 200, { ok: true, changed: false, count: merged.length });
      }

      const msg = `chore(fav): sync user favorites (${merged.length} items)`;
      await ghPut(FILE_PATH, mergedStr, sha, msg);

      return j(res, 200, { ok: true, changed: true, count: merged.length });
    }

    return j(res, 405, { error: 'method not allowed' });
  } catch (err) {
    console.error('[/api/fav] error:', err);
    return j(res, 500, { error: String(err.message || err) });
  }
}
