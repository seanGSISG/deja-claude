#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$PLUGIN_ROOT/hooks/lib.sh"

PYTHON="$(resolve_python)"

INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))")

SESSION_LOG=$(get_session_log "$SESSION_ID")
ensure_dir "$(dirname "$SESSION_LOG")"

# Build and append JSONL event using Python for correct JSON encoding
{
    echo "$INPUT" | "$PYTHON" -c "
import sys, json
from datetime import datetime, timezone

d = json.load(sys.stdin)
ti = d.get('tool_input', {})
tn = d.get('tool_name', '')
files = []
summary = ''

if tn == 'Edit':
    fp = ti.get('file_path', '')
    old = ti.get('old_string', '')[:50]
    new = ti.get('new_string', '')[:50]
    summary = f'Edit {fp}: {old} -> {new}'
    files = [fp] if fp else []
elif tn == 'Write':
    fp = ti.get('file_path', '')
    content = ti.get('content', '')
    lines = content.count(chr(10)) + 1
    summary = f'Write {fp} ({lines} lines)'
    files = [fp] if fp else []
elif tn == 'Bash':
    cmd = ti.get('command', '')[:100]
    summary = f'Bash: {cmd}'
elif tn == 'Read':
    fp = ti.get('file_path', '')
    summary = f'Read {fp}'
    files = [fp] if fp else []
elif tn in ('Grep', 'Glob'):
    pat = ti.get('pattern', '')
    summary = f'Search: {pat}'
else:
    summary = tn

event = {
    'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'event_type': 'tool_use',
    'tool_name': tn,
    'tool_input_summary': summary,
    'files_touched': files
}
print(json.dumps(event))
"
} >> "$SESSION_LOG" &

exit 0
