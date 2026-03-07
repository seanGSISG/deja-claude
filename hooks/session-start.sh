#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$PLUGIN_ROOT/hooks/lib.sh"

PYTHON="$(resolve_python)"

INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))")

# Register session in SQLite
BRANCH=$(git branch --show-current 2>/dev/null || echo "")
WORKING_DIR=$(pwd)

"$PYTHON" -c "
import sys; sys.path.insert(0, '$PLUGIN_ROOT/scripts')
from storage import store_session
store_session('$SESSION_ID', branch='$BRANCH', working_dir='$WORKING_DIR')
" 2>/dev/null || true

# Inject memory context — plain text stdout + exit 0 = context injected
CONTEXT=$("$PYTHON" "$PLUGIN_ROOT/scripts/inject.py" --session-id "$SESSION_ID" 2>/dev/null || echo "")

if [ -n "$CONTEXT" ]; then
    echo "$CONTEXT"
fi

exit 0
