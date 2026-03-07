#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$PLUGIN_ROOT/hooks/lib.sh"

INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | extract_json_field "session_id")

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
SESSION_LOG=$(get_session_log "$SESSION_ID")
ensure_dir "$(dirname "$SESSION_LOG")"

# Append pre_compact event
{
    echo "{\"timestamp\":\"${TIMESTAMP}\",\"event_type\":\"pre_compact\",\"context_summary\":\"Context compaction triggered\"}"
} >> "$SESSION_LOG" &

exit 0
