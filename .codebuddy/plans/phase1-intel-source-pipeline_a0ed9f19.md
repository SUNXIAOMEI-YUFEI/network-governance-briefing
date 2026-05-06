---
name: phase1-intel-source-pipeline
overview: Phase 1：把"域外网络治理情报信源"流水线从 OPML 静态文件升级为可日常运转的 Inoreader 订阅系统，包含 38 个 RSS feed 健康度预检、Inoreader 5 分钟上手 SOP、过去 7 天信源预演样本三件交付物，验收后再进入 Phase 2 固化 skill。
todos:
  - id: recon-existing-assets
    content: 通读 research_cac_profile.md 和 research_intel_sources.md，提取 38 个 RSS feed 完整 URL 列表 + 8 大焦虑点关键词词表
    status: completed
  - id: rss-health-check
    content: 使用 [subagent:code-explorer] 批量 web_fetch 38 个 RSS 源，输出 phase1_step1_rss_health_report.md（含存活状态/最近更新/失效源替代方案）
    status: completed
    dependencies:
      - recon-existing-assets
  - id: opml-v2
    content: 基于健康度报告产出 phase1_step2_opml_v2.opml，按 8 大焦虑点分文件夹，剔除死链、替换为 RSSHub 路由
    status: completed
    dependencies:
      - rss-health-check
  - id: inoreader-sop
    content: 编写 phase1_step3_inoreader_sop.md，含注册/导入 OPML/文件夹结构/关键词高亮规则模板/移动端同步/团队共享六节，每步 ≤ 30 秒
    status: completed
    dependencies:
      - opml-v2
  - id: intel-preview-7days
    content: 用 web_search 检索 2026-04-21 至 2026-04-28 过去 7 天的域外动态，按 8 大焦虑点归类，输出 phase1_step4_intel_preview_7days.md 预演样本
    status: completed
    dependencies:
      - recon-existing-assets
  - id: handoff-checklist
    content: 编写 phase1_handoff_to_user.md，给出用户 Step 1.4/1.5 自检清单（导入是否成功、3-5 天后信噪比反馈表），并预告 Phase 2 入口条件
    status: completed
    dependencies:
      - inoreader-sop
      - intel-preview-7days
      - opml-v2
---

## 用户需求

将《网络治理动态速递》的工作流水线从 0 搭起来，按"信源 → 选题优先级 → 选题筛选 → 试写"四步顺序逐环跑通。当前任务**仅聚焦 Phase 1：信源跑通**，不涉及后续任何写作或 skill 文件落盘。

## 核心目标

让用户（一位完全不懂 RSS/Reader 的法律研究人员）在 Inoreader 上建成一个**真实可用、信噪比可验证**的"个人监管情报中台"，作为后续选题筛选与成稿的素材源头。

## 核心交付物

1. **38 个 RSS 源健康度报告** —— 逐一预检 `research_intel_sources.md` 附录 A 中登记的 38 个 feed，标注存活状态（✅可用 / ⚠️需 RSSHub 替代 / ❌已死链），给出最近更新时间和近 30 天产文频率，对失效源给出替代方案（官方源直连 / RSSHub 路由 / Kill the Newsletter! 邮件转 RSS）。

2. **修订版 OPML 文件** —— 基于健康度报告产出 v2 OPML，用户在 Inoreader 一键导入即可，不会出现红字报错。

3. **Inoreader 5 分钟上手 SOP** —— 中文图文步骤：注册账号 → 导入 OPML → 按 8 大焦虑点建文件夹 → 设置关键词高亮规则（China/minor/GDPR/agent/synthetic 等）→ 移动端同步 → 团队共享设置。

4. **过去 7 天信源预演样本** —— 用 web_search/web_fetch 模拟"Inoreader 跑起来后会看到什么"，产出 2026-04-21 至 2026-04-28 的情报快照（按 8 大焦虑点分类、标注信源等级），让用户在真正动手前先看到"未来收件箱长什么样"。

## 验收标准

- 用户拿到三份 markdown 交付物 + 一份 OPML 文件
- 用户按 SOP 完成 Inoreader 配置（5-10 分钟亲自动手）
- 用户跑 3-5 天后能反馈"实际信噪比"，进入 Phase 2

## 范围边界

- 本 plan **不写**任何 skill 文件（Phase 2 才做）
- 本 plan **不写**第 3 期速递（Phase 4 才做）
- 本 plan **不替**用户操作账号（Step 1.4/1.5 用户亲自做）

## 技术方案

### 工作模式

- **只读模式（ask 模式）下完成**：所有产出限于 `web_fetch`（探活 RSS）+ `web_search`（预演样本检索）+ markdown 文件落盘
- **不涉及任何代码工程**：纯研究 + 文档型交付

### 交付物落盘位置

统一落到 artifact 目录：

```
/Users/pheobezhong/Library/Application Support/CodeBuddy CN/User/globalStorage/tencent-cloud.coding-copilot/brain/c4e04c6855da4793941fcc6bc2c22342/
```

命名遵循 `phase1_*` 前缀，便于按阶段检索。

### 关键技术决策

**1. RSS 探活策略（核心瓶颈）**

- 对每个 feed URL 用 `web_fetch` 拉取，判断三个维度：
- **HTTP 状态**：200 / 301 / 404 / 410 / 超时
- **格式合法性**：返回内容是否为合法 XML/Atom（含 `<rss>` 或 `<feed>` 根节点）
- **新鲜度**：解析最新一条 entry 的 pubDate，距今 ≤ 30 天 = 活跃，30-90 天 = 半休眠，>90 天 = 疑似废弃
- **失败回退**：HTTP 失败的源，尝试 ① RSSHub 公共实例（`rsshub.app/...`）② 官网寻找新 RSS 入口 ③ Kill the Newsletter! 邮件转 RSS 方案
- **批处理**：分组并发探活（每组 5-8 个），避免阻塞，整体控制在 1 次任务内完成

**2. 7 天预演样本检索策略**

- 不依赖 Inoreader（用户还没装），而是用 `web_search` 直接模拟 reader 的输出
- 检索关键词来自 `research_cac_profile.md` 8 大焦虑点 + `research_intel_sources.md` 关键人物名单的笛卡尔积，例如：
- "EU AI Act enforcement" + 时间过滤 past week
- "FTC minor online safety" + past 7 days
- "synthetic media labeling" + past week
- 律所博客直接 site:cov.com / site:wilmerhale.com 限定
- 输出按 8 大焦虑点归类，每条标注【信源 / 时间 / 一句话摘要 / 网信办关切对应度（高/中/低）】

**3. Inoreader SOP 编写原则**

- **零基础视角**：假设用户从未听说 RSS、不知道 OPML 怎么导
- **每步 ≤ 30 秒**：把"5 分钟上手"拆成 8-10 个原子步骤
- **关键词规则给现成模板**：直接给可复制粘贴的规则表（中英文关键词对应 8 大焦虑点）
- **文件夹结构按"焦虑点维度"组织**（不按"信源类型"维度），让收件箱一打开就是"政治内容失控 / 未成年人 / 标识失效 ..."八个抽屉，与速递选题逻辑天然对齐

### 文件清单

```
artifact-dir/
├── phase1_step1_rss_health_report.md    # [NEW] 38 个 RSS 探活报告 + 失效源替代方案
├── phase1_step2_opml_v2.opml            # [NEW] 修订版 OPML（基于健康度报告剔除/替换死链）
├── phase1_step3_inoreader_sop.md        # [NEW] Inoreader 5 分钟上手 SOP（含关键词规则模板 + 文件夹结构建议）
├── phase1_step4_intel_preview_7days.md  # [NEW] 过去 7 天信源预演样本（按 8 大焦虑点归类）
└── phase1_handoff_to_user.md            # [NEW] 给用户的"下一步动作清单"，含 Step 1.4/1.5 自检表
```

### 实施注意事项

- **不要重写已有方法论资产**：`research_cac_profile.md` 和 `research_intel_sources.md` 是基础，本 plan 只做"补全 + 验证 + 落地"，避免重复劳动
- **探活失败的源**：不要静默删除，必须在健康度报告里留痕（"原 X feed 已失效，替代为 Y"），便于追溯
- **预演样本不强求穷尽**：每个焦虑点 3-5 条即可，目的是让用户感性认知"reader 跑起来什么样"，不是真正选题
- **OPML 文件格式**：严格遵循 OPML 2.0 规范，category 用 `text` 属性区分 8 大焦虑点文件夹
- **Inoreader SOP 中所有截图位置用占位符**：`[截图：导入 OPML 界面]`，避免在只读模式下尝试生成图片

## Agent Extensions

### SubAgent

- **code-explorer**
- Purpose: 当 38 个 RSS 探活分组并发任务量较大时，用于批量调度 web_fetch 请求并汇总结果
- Expected outcome: 一份结构化的"URL → 状态码 → 最后更新 → 建议动作"四列对照表，作为健康度报告的原始数据