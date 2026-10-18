#!/bin/bash
# SubagentStop + PostToolUse(mcp__local-*): one JSONL line per event.
set -euo pipefail
input=$(cat)
log="${CLAUDE_PROJECT_DIR:-.}/.pipeline/audit.jsonl"; mkdir -p "$(dirname "$log")"
jq -c '{ts:(now|todate), event:.hook_event_name, agent_id:(.agent_id//null),
        agent_type:(.agent_type//null), tool:(.tool_name//null), session:.session_id}' \
  <<<"$input" >> "$log" || true
exit 0
