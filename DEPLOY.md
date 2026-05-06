# 部署到公开 URL · 操作清单（为 @SUNXIAOMEI-YUFEI 定制）

> 目标：**任何设备打开一个 URL 看到每天自动更新的选题工作台**。
> 架构：GitHub Actions（每天 14:00 自动跑后端流水线） + Vercel（托管前端 + 静态 JSON）
> 工作量：**10-15 分钟**（一次性配置）
> GitHub：https://github.com/SUNXIAOMEI-YUFEI

---

## 一览

```
你每天早上：
    打开 https://xxx.vercel.app → 看到 Top 3 → 写速递。完。

幕后（自动）：
    GitHub Actions cron（UTC 06:00 = 北京 14:00）
    ├─ 抓 37 个 RSS（境外服务器直连，无 VPN 障碍）
    ├─ DeepSeek 评分（国内国外都通）
    ├─ 聚类 + 出 today.json
    └─ git push 到仓库 → Vercel 自动重新部署前端
```

---

## Step 1 · 准备 GitHub 仓库（3 分钟）

### 1.1 没 GitHub 账号的话先注册
https://github.com/signup（用邮箱注册，全程 2 分钟）

### 1.2 开一个新仓库
1. 登录 GitHub → 右上角 `+` → **New repository**
2. 填：
   - Repository name：`network-governance-briefing`（或别的你喜欢的名）
   - **Public**（公开。原因见下方 FAQ；如需私有切 Private 也行）
   - **不要**勾任何 "Add a README / .gitignore / license"（我们本地已经有了）
3. 点 **Create repository**
4. 页面会跳到一个"quick setup"页，**复制那串 `git@github.com:你的用户名/network-governance-briefing.git` 或 `https://github.com/你的用户名/...git`** —— 下一步要用

---

## Step 2 · 把本地代码 push 上去（2 分钟）

**打开 Terminal**，一步步执行：

```bash
cd /Users/pheobezhong/CodeBuddy/20260428135716

# 初始化 git（如果还没）
git init
git branch -M main

# 看看 .gitignore 有没有把 .env 排除（应该有）
grep -q "^.env$" .gitignore && echo "✅ .env 已在 gitignore" || echo "❌ .env 未 gitignore，要补"

# 首次提交
git add -A
git status   # 目测一下别把 .env 提进去了
git commit -m "initial: briefing project scaffold"

# 绑仓库（把 URL 换成 Step 1.2 给你的那串）
git remote add origin https://github.com/你的用户名/network-governance-briefing.git

# 推上去
git push -u origin main
```

> 如果 `git push` 让你登录：用浏览器登录 GitHub → Settings → Developer settings → Personal access tokens → 创一个 classic token（勾 `repo` 权限）→ 粘贴到 terminal 提示你输 password 的地方（复制后看不到，直接回车）。

### 2.1 验证
GitHub 仓库页刷新 → 应该能看到所有文件（`app/`、`v2/`、`data/`、`.github/` 等）。

---

## Step 3 · 把 DeepSeek API Key 配进 GitHub Secrets（2 分钟）

**这一步至关重要**——Actions 跑评分要用它，但我们不能把 key 写在代码里。

1. GitHub 仓库页 → **Settings**（右上那个，不是账户的）
2. 左侧边栏 → **Secrets and variables** → **Actions**
3. 点 **New repository secret**
4. 填：
   - Name：`LLM_API_KEY`
   - Secret：`sk-c4b9c342c8d84f148f3ece04f73f4598`（DeepSeek 官方 key）
5. **Add secret**

（可选）如果未来你想切到别的 AI 服务：

- `LLM_BASE_URL`（不配置默认 `https://api.deepseek.com/v1`）
- `LLM_MODEL`（不配置默认 `deepseek-chat`）

---

## Step 4 · 手动触发一次 Actions 验证（3 分钟）

1. 仓库页 → **Actions** 标签
2. 左侧列表点 **Daily Briefing Pipeline**
3. 右上角 **Run workflow** → **Run workflow**（默认参数就行）
4. 等 1-2 分钟，看到绿色 ✅ = 成功

### 如果失败了怎么办
点进那次失败的 run，展开 **Run daily pipeline** 步骤看日志。常见问题：

| 报错 | 原因 | 修法 |
|---|---|---|
| `缺少 LLM_API_KEY` | Step 3 的 Secret 没配对 | 回 Step 3 检查 Name 拼写（必须 `LLM_API_KEY`） |
| RSS fetch 几个超时 | 个别源偶发 timeout | **不影响**，workflow 设计成不阻塞，其他源照抓 |
| DeepSeek 401 | key 过期或错误 | 重新从 platform.deepseek.com 拿个新 key 更新 Secret |
| `git push` 权限不足 | workflow 缺 permissions | 已在 yml 里配了 `contents: write`，理论上不会发生 |

---

## Step 5 · Vercel 部署（3 分钟）

### 5.1 登录 Vercel
https://vercel.com/login → Continue with GitHub（用你刚才的 GitHub 账号登录）

### 5.2 导入仓库
1. Vercel 主页点 **Add New...** → **Project**
2. 找到刚才推的那个仓库 `network-governance-briefing` → **Import**
3. Configure Project 页：
   - Framework Preset：**Other**（它会自动识别，如果没有选 Other 即可）
   - Root Directory：保留默认 `./`
   - Build Command：**留空**（我们是纯静态，没有 build 步骤）
   - Output Directory：**留空**
4. **Deploy**

### 5.3 拿到 URL
30-60 秒后部署完成。页面会给你一个 URL：

```
https://network-governance-briefing-xxx-你的用户名.vercel.app
```

或你可以 Settings → Domains 换个好记的（比如 `briefing.你的用户名.vercel.app`，免费）。

### 5.4 打开看看
这个 URL 现在应该能看到你昨天看的那份工作台（Top 3 + 议题聚类 + 情报池 全家桶）。

---

## Step 6 · 每天自动更新（已经好了）

你不需要做任何事：

- GitHub Actions 每天 UTC 06:00（北京 14:00）自动跑
- 跑完推 `data/today.json` 新版本到仓库
- Vercel 检测到 git push，自动重新部署（30 秒）
- 你 14:30 刷新 URL 就是最新选题

**唯一要记的**：你的 URL。加书签、加 iPhone 主屏快捷方式、分享给团队——这就是你每天的唯一入口。

---

## 常见问题 FAQ

### Q1: 为什么推荐公开仓库？

**公开仓库的好处**：
- Actions 免费无限额度（私有只有 2000 分钟/月，够但紧张）
- Vercel 免费额度更慷慨
- 配置更简单（权限问题都不存在）

**公开暴露的东西**：
- 代码（纯技术栈，无公司机密）
- `data/today.json` 里的选题（都是基于公开 RSS，本来就公开）
- mock 数据（虚构的 15 条）

**公开不会暴露**：
- DeepSeek API key（走 Secrets 加密）
- 你的 `.env` 文件（已 gitignore）
- KtN 邮箱地址（.env 里，未进仓库）
- 你点的 👍/👎（存 localStorage，不进仓库）
- 你写成的速递正文（不在这项目里）

### Q2: 想切私有怎么办？

1. GitHub 仓库 Settings → General → 最下方 Danger Zone → **Change visibility** → Private
2. Vercel 那边 Settings → Git → 重新 connect（可能需要授权 Vercel 访问私有仓库，点同意即可）

### Q3: 想手动触发一次怎么办？

GitHub 仓库页 → Actions → Daily Briefing Pipeline → Run workflow（右上角）

### Q4: 想暂停自动运行怎么办？

GitHub 仓库页 → Actions → Daily Briefing Pipeline → 右上角 `...` 菜单 → **Disable workflow**。想恢复就 Enable。

### Q5: 想换跑的时间（不要 14:00）怎么办？

编辑 `.github/workflows/daily.yml` 里的 `cron: "0 6 * * *"`（这个是 UTC 时间，想跑北京 X 点就填 `X - 8`）：

```
北京 07:00  →  cron: "0 23 * * *"   (UTC 前一天 23:00)
北京 09:00  →  cron: "0 1 * * *"
北京 14:00  →  cron: "0 6 * * *"     ← 当前
北京 18:00  →  cron: "0 10 * * *"
```

### Q6: Vercel 免费额度会不会被用完？

按官方给的 Hobby 计划：
- 静态部署：无限
- 每月带宽：100 GB（你这个小网页一年也用不掉 1 GB）
- 重新部署次数：无限

除非你每天刷屏几千次，否则完全用不完。

### Q7: 某天网页没更新怎么排查？

按顺序查：
1. GitHub 仓库页 → Actions → 看今天那次 run 是不是 ✅
   - ❌ 失败 → 点进去看 Run daily pipeline 步骤的错误
   - ⚠️ 正在排队 → GitHub 偶尔有队列延迟，等 30 分钟再看
2. `data/today.json` 在仓库里最近 commit 时间 → 是不是今天
3. Vercel 主页 → 项目 → Deployments → 最近一次是不是 today.json 那个 commit

---

## 完成标志

✅ GitHub 仓库能看到所有代码
✅ Actions 跑一次是绿色的 ✅
✅ `data/today.json` 有被 commit 更新
✅ Vercel URL 能打开，看到今日选题
✅ 把 Vercel URL 加到了手机主屏 / 书签

以上都对 = 项目正式上线 🎉 你每天的全部交互就是**打开那个 URL**。
