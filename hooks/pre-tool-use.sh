#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
source "$PLUGIN_ROOT/hooks/lib.sh"

PYTHON="$(resolve_python)"

INPUT=$(cat)

# Fast-path filter: only check Bash/Write/Edit tools
TOOL_NAME=$(echo "$INPUT" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null || echo "")

case "$TOOL_NAME" in
    Bash|Write|Edit)
        # Delegate to gate_check.py for danger pattern matching + memory search
        echo "$INPUT" | "$PYTHON" "$PLUGIN_ROOT/scripts/gate_check.py" 2>/dev/null || true
        ;;
esac

# Advisory only — never block
exit 0
