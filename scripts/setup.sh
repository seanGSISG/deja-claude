#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$HOME/.config/claude-memory/.venv"
CONFIG_DIR="$HOME/.config/claude-memory"

echo "Setting up deja-claude (claude-memory plugin)..."

# 1. Validate Python 3.10+
python3 -c "import sys; assert sys.version_info >= (3, 10), f'Python 3.10+ required, got {sys.version}'" || {
    echo "ERROR: Python 3.10+ is required"
    exit 1
}

# 2. Create config directory
mkdir -p "$CONFIG_DIR"

# 3. Create venv at ~/.config/claude-memory/.venv (survives plugin cache wipes)
if [ ! -d "$VENV_DIR" ]; then
    if command -v uv > /dev/null 2>&1; then
        uv venv "$VENV_DIR"
    else
        python3 -m venv "$VENV_DIR"
    fi
fi

# 4. Install Python dependencies into venv
VENV_PYTHON="$VENV_DIR/bin/python3"
if [ -f "$PLUGIN_ROOT/requirements.txt" ]; then
    if command -v uv > /dev/null 2>&1; then
        source "$VENV_DIR/bin/activate"
        uv pip install --quiet -r "$PLUGIN_ROOT/requirements.txt" 2>/dev/null || true
        deactivate 2>/dev/null || true
    elif [ -x "$VENV_PYTHON" ]; then
        "$VENV_PYTHON" -m pip install --quiet -r "$PLUGIN_ROOT/requirements.txt" 2>/dev/null || true
    fi
fi

# 5. Create default config if missing
if [ ! -f "$CONFIG_DIR/config.json" ]; then
    cat > "$CONFIG_DIR/config.json" <<'DEFAULTCONFIG'
{
  "provider": "google",
  "model": "gemini-3.1-flash-lite-preview",
  "google_api_key_env": "GOOGLE_API_KEY",
  "anthropic_api_key_env": "ANTHROPIC_API_KEY",
  "openai_api_key_env": "OPENAI_API_KEY",
  "fallback_to_claude": true,
  "claude_model": "haiku"
}
DEFAULTCONFIG
fi

# 6. Create memory directories
MEMORY_DIR="${CLAUDE_PROJECT_DIR:-$HOME/.claude/projects/default}/claude-memory"
mkdir -p "$MEMORY_DIR/sessions"

# 7. Initialize database
"$VENV_PYTHON" "$PLUGIN_ROOT/scripts/storage.py" --init 2>/dev/null || \
    python3 "$PLUGIN_ROOT/scripts/storage.py" --init

echo "deja-claude plugin ready."
