# 外部调度器接管 cron（cron-job.org）

## 为什么要做这件事

GitHub Actions 免费版 `schedule:` cron **事实上不可靠**：

- 官方 docs 明确说：高峰期会延迟甚至跳过
- 本仓库实测（2026-04-28 ~ 2026-05-08）：设定过 `0 6`、`17 6`、`17 7` 三轮 cron，GitHub 真正触发的 scheduled run **只有 1 次**（而且还延迟了 7 小时，跑到了 UTC 13:20）
- 已经加了错峰双触发（14:17 + 15:17），但今天（5/8）两场**同时被跳过**

结论：**schedule 不能作为生产流水线的主触发机制**。改为 `workflow_dispatch` + 外部 HTTP 定时器触发，准时率接近 100%。

已有兜底（留着不动）：

- `schedule: "17 6 * * *"` 和 `"17 7 * * *"` 作为双保险
- 前端 `last_run.json > 18h` 红条告警，万一外部 cron 也挂了能看到

---

## 架构

```
cron-job.org (免费，每天北京 14:15 HTTP POST)
      │
      │  POST /repos/.../actions/workflows/daily.yml/dispatches
      │  Authorization: Bearer <fine-grained PAT>
      ▼
GitHub Actions workflow_dispatch event
      │
      ▼
daily.yml 流水线（真实数据路径，非 mock）
      │
      ▼
commit data/today.json + data/last_run.json
```

---

## 一、生成 fine-grained PAT（5 分钟）

**为什么 fine-grained 不用 classic**：fine-grained 可以限定**只对单个仓库生效**，且权限粒度到 workflow 级。即便 token 泄漏，blast radius 也仅限本仓库。

1. 打开 https://github.com/settings/personal-access-tokens/new
2. 表单填写：
   - **Token name**：`briefing-workflow-trigger`
   - **Expiration**：`1 year`（到期前 GitHub 会邮件提醒）
   - **Resource owner**：选你自己（SUNXIAOMEI-YUFEI）
   - **Repository access** → 选 **Only select repositories** → 勾 `network-governance-briefing`
   - **Permissions** → 展开 **Repository permissions**，找到：
     - **Actions**：`Read and write`（用来触发 workflow_dispatch）
     - 其他**全部保持 No access** ← 这点很重要
3. 点 **Generate token**，复制出来（形如 `github_pat_11ABC...`），立即妥善保存（离开页面就看不到了）

**PAT 最小权限原则校验**：
- ✅ 只能操作 `network-governance-briefing` 一个仓库
- ✅ 只有 Actions 读写权限（无 code / contents / secrets / issues / PR）
- ✅ 1 年自动过期
- ❌ 不能读代码、不能改代码、不能碰 secrets

---

## 二、本地自测（1 分钟，确认 PAT 能用）

在**临时 shell 会话**里验证一次（切勿写进 `.zshrc` / `.env` / 任何文件）：

```bash
cd /Users/pheobezhong/CodeBuddy/20260428135716

# 注意：粘贴后立即 history -d 或关终端
export GH_PAT='github_pat_11ABC...你刚才复制的 PAT'

bash scripts/trigger_workflow.sh
```

预期输出：

```
→ POST https://api.github.com/repos/SUNXIAOMEI-YUFEI/network-governance-briefing/actions/workflows/daily.yml/dispatches
  ref=main  hours=24  use_mock=false  rescore_all=false
← HTTP 204
✅ 已触发，3-5 分钟后去 Actions 看结果：
   https://github.com/SUNXIAOMEI-YUFEI/network-governance-briefing/actions/workflows/daily.yml
```

然后去 https://github.com/SUNXIAOMEI-YUFEI/network-governance-briefing/actions 看是不是出了一条 `workflow_dispatch` 事件的 run，3-5 分钟后 commit 也会出来。

**自测通过后关掉终端**，`GH_PAT` 环境变量会随 shell 退出清掉。如果没有通过，看 HTTP 错误码：
- 401：PAT 无效或没带对
- 403：PAT 权限不够（检查是否给了 Actions: Read and write）
- 404：仓库名 / workflow 文件名拼错了

---

## 三、配置 cron-job.org（5 分钟）

**为什么选 cron-job.org**：
- 免费、无需信用卡、无调用次数限制（公共任务上限 50 个够用）
- 界面干净，不塞广告，德国公司 GDPR 合规
- 支持自定义 HTTP headers（放 PAT 必须）
- 有执行历史、失败邮件告警

### 步骤

1. 打开 https://cron-job.org/en/ → 右上 **Sign up**，用 `1350428257@qq.com` 注册（沿用项目邮箱，便于归档）
2. 确认邮箱登录后，点 **Cronjobs** → **CREATE CRONJOB**
3. **Common** 标签页：
   - **Title**：`Briefing daily pipeline`
   - **URL**：
     ```
     https://api.github.com/repos/SUNXIAOMEI-YUFEI/network-governance-briefing/actions/workflows/daily.yml/dispatches
     ```
   - **Enabled**：✅ 打勾
   - **Save responses**：✅ 打勾（失败时能看到 GitHub 返回的错误）
4. **Schedule** 标签页：
   - **Timezone**：`Asia/Shanghai`（**非常重要**，默认是 UTC）
   - 选 **Schedule**，填：
     - **Days**：Every day
     - **Hours**：`14`
     - **Minutes**：`15`
   - 也就是北京时间每天 14:15 触发
5. **Advanced** 标签页：
   - **Request method**：`POST`
   - **Request timeout**：`30 seconds`
   - **Treat redirects as success**：（不用勾）
6. **Notifications** 标签页：
   - 勾选 **On failure: notify me by email**
   - （可选）**On disable: notify me by email**
7. **Headers** 标签页（关键）：添加以下 3 条，每条点 **ADD HEADER**：

   | Name | Value |
   |---|---|
   | `Accept` | `application/vnd.github+json` |
   | `Authorization` | `Bearer github_pat_11ABC...你的 PAT` |
   | `X-GitHub-Api-Version` | `2022-11-28` |

8. **Body** 标签页：
   - **Request body type**：`Raw`
   - **Content type**：`application/json`
   - **Body**：
     ```json
     {"ref":"main","inputs":{"hours":"24","use_mock":false,"rescore_all":false}}
     ```
9. 点底部 **CREATE**

10. 回到 Cronjobs 列表，找到刚建的 job → 右侧三点菜单 → **Execute now** → 立即跑一次验证
11. 点 job 名字进入详情 → **History** 标签 → 看最新一行，**Status: 204, Duration: 毫秒级** 即正确
12. 同时 https://github.com/.../actions 会出现新的 `workflow_dispatch` run

### 备份场次（可选但推荐）

再建一个 Cronjob，标题改成 `Briefing daily pipeline (backup)`，时间改成 **15:17**（北京时间），其他全部一样。理由同 daily.yml 的兜底策略：首跑成功后第二次会自动 skip commit，花不了几毛钱的 Actions 额度，但能把"被任何一侧卡住"的概率再砍一半。

---

## 四、PAT 安全守则

- ✅ PAT **只存在于 cron-job.org 的 Headers 里**，其他地方都不要存
- ✅ 本仓库的 `.env`、`.gitignore`、任何配置文件**都不要放 PAT**
- ✅ 本地 shell 自测用 `export GH_PAT=...` 临时注入，关终端自动消失
- ❌ 不要 commit，不要 push 到任何 git 仓库
- ❌ 不要贴到聊天记录、不要截图
- ⏰ 设提醒：**2027-05-08 前**去 https://github.com/settings/personal-access-tokens 续期或重新生成
- 🚨 一旦怀疑泄漏：立即到 PAT 页面 **Revoke**，然后按本文重建一个

### 万一泄漏了会发生什么？

因为是 fine-grained + 仅 Actions: Read and write + 仅本仓库：

- 攻击者**可以**触发你的 workflow_dispatch（最多让你的 CI 多跑几次，消耗 Actions minutes，公开 repo 免费额度 2000 分钟/月）
- 攻击者**无法**读/改你的代码
- 攻击者**无法**读/改你的 repository secrets（LLM_API_KEY 等都安全）
- 攻击者**无法**操作你名下其他仓库

风险等级：低。但仍应立即 revoke。

---

## 五、验证清单（完成后逐条 ✅）

- [ ] PAT 已生成，过期时间 2027-05-08
- [ ] 本地 `bash scripts/trigger_workflow.sh` 返回 HTTP 204
- [ ] GitHub Actions 有了一条 `workflow_dispatch` 事件的 success run
- [ ] cron-job.org 主场次（14:15）已创建 + Execute now 验证 204
- [ ] cron-job.org 备份场次（15:17）已创建（可选）
- [ ] `data/last_run.json` 时间戳刷新为刚才的触发时间
- [ ] 本仓库 `.env`、`.zshrc`、`git log -p` 中均无 PAT 明文
- [ ] 日历提前 30 天设一次"PAT 续期"提醒

全部 ✅ 后，就可以把 GitHub schedule 当成第三道兜底（万一 cron-job.org 挂了），不必再关心 14:17 / 15:17 那两场是否真跑。前端红条告警会在所有路径都失败超 18h 时弹出，不会再有"静默漏跑"。
