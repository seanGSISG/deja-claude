#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
source "$PLUGIN_ROOT/hooks/lib.sh"

PYTHON="$(resolve_python)"

INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))")
TRANSCRIPT=$(echo "$INPUT" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('transcript_path',''))")

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
SESSION_LOG=$(get_session_log "$SESSION_ID")
ensure_dir "$(dirname "$SESSION_LOG")"

# Append session_end event
echo "{\"timestamp\":\"${TIMESTAMP}\",\"event_type\":\"session_end\"}" >> "$SESSION_LOG"

# End session in SQLite — pass via env vars to avoid injection
SESSION_ID="$SESSION_ID" PLUGIN_ROOT="$PLUGIN_ROOT" \
"$PYTHON" -c "
import os, sys; sys.path.insert(0, os.environ.get('PLUGIN_ROOT', '.') + '/scripts')
from storage import end_session
end_session(os.environ['SESSION_ID'])
" 2>/dev/null || true

# Spawn extraction in background with env vars propagated
if [ -n "$SESSION_ID" ] && [ -n "$TRANSCRIPT" ]; then
    nohup env \
        CLAUDECODE="" \
        GOOGLE_API_KEY="${GOOGLE_API_KEY:-}" \
        ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
        OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
        CLAUDE_MEMORY_API_KEY="${CLAUDE_MEMORY_API_KEY:-}" \
        CLAUDE_MEMORY_PROVIDER="${CLAUDE_MEMORY_PROVIDER:-google}" \
        CLAUDE_PROJECT_DIR="${CLAUDE_PROJECT_DIR:-}" \
        CLAUDE_MEMORY_DB_PATH="${CLAUDE_MEMORY_DB_PATH:-}" \
        "$PYTHON" "$PLUGIN_ROOT/scripts/extract.py" \
            --session-id "$SESSION_ID" \
            --transcript "$TRANSCRIPT" \
        > /dev/null 2>&1 &
fi

exit 0
