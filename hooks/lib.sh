#!/usr/bin/env bash
# Shared shell functions for claude-memory hooks

resolve_config_dir() {
    echo "$HOME/.config/claude-memory"
}

get_memory_dir() {
    local project_dir="${CLAUDE_PROJECT_DIR:-$HOME/.claude/projects/default}"
    echo "${project_dir}/claude-memory"
}

get_session_log() {
    local session_id="$1"
    local memory_dir
    memory_dir="$(get_memory_dir)"
    echo "${memory_dir}/sessions/${session_id}.jsonl"
}

ensure_dir() {
    local dir="$1"
    [ -d "$dir" ] || mkdir -p "$dir"
}

resolve_python() {
    # Priority 1: Config dir venv (survives plugin cache wipes)
    local config_venv="$HOME/.config/claude-memory/.venv/bin/python3"
    if [ -x "$config_venv" ]; then
        echo "$config_venv"
        return
    fi
    # Priority 2: Plugin-relative venv (development mode)
    local plugin_root="${PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
    local dev_venv="${plugin_root}/../.venv/bin/python3"
    if [ -x "$dev_venv" ]; then
        echo "$dev_venv"
        return
    fi
    # Priority 3: System python3
    echo "python3"
}

extract_json_field() {
    local field="$1"
    local python
    python="$(resolve_python)"
    "$python" -c "import sys,json; print(json.load(sys.stdin).get('$field',''))"
}
