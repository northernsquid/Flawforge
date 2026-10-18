#!/bin/bash
# Stop hook: whole-tree Semgrep sweep; blocks "done" until clean.
set -euo pipefail
input=$(cat)
[ "$(jq -r '.stop_hook_active // false' <<<"$input")" = "true" ] && exit 0
cd "${CLAUDE_PROJECT_DIR:-.}"
[ -z "$(git status --porcelain 2>/dev/null)" ] && exit 0
report=$(/home/agent/.venv-tools/bin/semgrep --config p/default --config p/secrets --config p/owasp-top-ten --error --quiet --json 2>/dev/null || true)
count=$(jq -r '.results | length' <<<"$report" 2>/dev/null || echo 0)
if [ "${count:-0}" -gt 0 ]; then
  jq -nc --arg r "Semgrep found $count issue(s); resolve before finishing." '{decision:"block", reason:$r}'
fi
exit 0
