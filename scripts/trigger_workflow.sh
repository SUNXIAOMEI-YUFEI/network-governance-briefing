#!/usr/bin/env bash
# ============================================================================
#  trigger_workflow.sh — 触发 daily.yml 的 workflow_dispatch
# ============================================================================
#
#  【用途】
#  GitHub Actions 免费版 cron schedule 事实上极不可靠（本仓库从 2026-04-28
#  建立到 2026-05-08，设定了多轮 cron 调度，GitHub 真正触发的 scheduled run
#  只有 1 次，而且还延迟了 7 小时）。所以改为由外部调度器（cron-job.org）
#  定时 HTTP 调用这个脚本，稳定把流水线跑起来。
#
#  【用法】
#  1) 本地验证（开发机一次性跑）：
#       export GH_PAT=ghp_xxx   # 只在 shell 会话里临时设置，不要入库
#       bash scripts/trigger_workflow.sh
#
#  2) 外部调度器（cron-job.org）：
#       不调这个脚本，直接按 CRON_EXTERNAL.md 里的 curl 命令配 HTTP job。
#       这个脚本本地验证用，也是 CRON_EXTERNAL.md 里 curl 命令的来源。
#
#  【参数】
#  - GH_PAT         必需，fine-grained PAT，权限见 CRON_EXTERNAL.md
#  - REPO_OWNER     默认 SUNXIAOMEI-YUFEI
#  - REPO_NAME      默认 network-governance-briefing
#  - WORKFLOW_FILE  默认 daily.yml
#  - BRANCH         默认 main
#  - HOURS          默认 24（抓取时间窗）
#  - USE_MOCK       默认 false（绝对不要改 true，会出 mock 数据）
#  - RESCORE_ALL    默认 false
#
#  【返回】
#  - 成功：HTTP 204，脚本 exit 0，并打印"已触发"提示
#  - 失败：打印 HTTP 状态码和响应体，脚本 exit 非 0
#
#  【安全】
#  - 脚本本身不带任何 secret，PAT 只从环境变量读
#  - 提交到公开仓库前请 grep 确认没有泄漏
# ============================================================================

set -euo pipefail

REPO_OWNER="${REPO_OWNER:-SUNXIAOMEI-YUFEI}"
REPO_NAME="${REPO_NAME:-network-governance-briefing}"
WORKFLOW_FILE="${WORKFLOW_FILE:-daily.yml}"
BRANCH="${BRANCH:-main}"
HOURS="${HOURS:-24}"
USE_MOCK="${USE_MOCK:-false}"
RESCORE_ALL="${RESCORE_ALL:-false}"

if [[ -z "${GH_PAT:-}" ]]; then
  echo "❌ 未设置 GH_PAT。用法：" >&2
  echo "    export GH_PAT=ghp_xxx" >&2
  echo "    bash scripts/trigger_workflow.sh" >&2
  exit 1
fi

URL="https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}/dispatches"

PAYLOAD=$(cat <<EOF
{
  "ref": "${BRANCH}",
  "inputs": {
    "hours": "${HOURS}",
    "use_mock": ${USE_MOCK},
    "rescore_all": ${RESCORE_ALL}
  }
}
EOF
)

echo "→ POST ${URL}"
echo "  ref=${BRANCH}  hours=${HOURS}  use_mock=${USE_MOCK}  rescore_all=${RESCORE_ALL}"

RESP_FILE=$(mktemp)
HTTP_CODE=$(curl -sS -o "${RESP_FILE}" -w "%{http_code}" \
  -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${GH_PAT}" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "${URL}" \
  -d "${PAYLOAD}")

echo "← HTTP ${HTTP_CODE}"

if [[ "${HTTP_CODE}" == "204" ]]; then
  echo "✅ 已触发，3-5 分钟后去 Actions 看结果："
  echo "   https://github.com/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}"
  rm -f "${RESP_FILE}"
  exit 0
else
  echo "❌ 触发失败，响应体："
  cat "${RESP_FILE}"
  echo
  rm -f "${RESP_FILE}"
  exit 2
fi
