#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$PLUGIN_ROOT/hooks/lib.sh"

INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | extract_json_field "session_id")

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
SESSION_LOG=$(get_session_log "$SESSION_ID")
ensure_dir "$(dirname "$SESSION_LOG")"

# Append stop event
{
    echo "{\"timestamp\":\"${TIMESTAMP}\",\"event_type\":\"stop\",\"reason\":\"session stopped\"}"
} >> "$SESSION_LOG" &

exit 0
