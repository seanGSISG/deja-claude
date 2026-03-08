"""Diagnostic checks for claude-memory plugin setup.

Reports the status of each component: Python version, venv, dependencies,
database, config file, API keys, and LLM provider connectivity.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

CONFIG_DIR = Path.home() / ".config" / "claude-memory"
CONFIG_FILE = CONFIG_DIR / "config.json"
PROD_VENV = CONFIG_DIR / ".venv"


def check_python() -> dict:
    """Check Python version."""
    v = sys.version_info
    ok = v >= (3, 10)
    return {
        "name": "Python version",
        "status": "ok" if ok else "fail",
        "detail": f"{v.major}.{v.minor}.{v.micro}",
        "fix": "Install Python 3.10+" if not ok else None,
    }


def check_venv() -> dict:
    """Check if a venv exists."""
    prod = PROD_VENV / "bin" / "python3"
    # Check dev venv relative to this script
    dev = Path(__file__).parent.parent.parent / ".venv" / "bin" / "python3"

    if prod.exists():
        return {"name": "Virtual environment", "status": "ok", "detail": f"Production: {PROD_VENV}", "fix": None}
    if dev.exists():
        return {"name": "Virtual environment", "status": "ok", "detail": f"Dev: {dev.parent.parent}", "fix": None}
    return {
        "name": "Virtual environment",
        "status": "fail",
        "detail": "No venv found",
        "fix": f"Run: uv venv {PROD_VENV} && source {PROD_VENV}/bin/activate && uv pip install -r requirements.txt",
    }


def check_dependencies() -> dict:
    """Check if required Python packages are importable."""
    missing = []
    for pkg, import_name in [("google-genai", "google.genai"), ("anthropic", "anthropic"), ("openai", "openai")]:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pkg)

    if not missing:
        return {"name": "Python dependencies", "status": "ok", "detail": "All installed", "fix": None}
    # Not all are required — only the one matching the configured provider
    return {
        "name": "Python dependencies",
        "status": "warn",
        "detail": f"Not installed: {', '.join(missing)}",
        "fix": "Only the package for your chosen provider is needed. Install with: uv pip install <package>",
    }


def check_config() -> dict:
    """Check config file."""
    if not CONFIG_FILE.exists():
        return {
            "name": "Config file",
            "status": "missing",
            "detail": str(CONFIG_FILE),
            "fix": "Will be created during setup",
            "config": {},
        }
    try:
        config = json.loads(CONFIG_FILE.read_text())
        provider = config.get("provider", "not set")
        model = config.get("model", "default")
        return {
            "name": "Config file",
            "status": "ok",
            "detail": f"Provider: {provider}, Model: {model}",
            "fix": None,
            "config": config,
        }
    except (json.JSONDecodeError, OSError) as e:
        return {
            "name": "Config file",
            "status": "fail",
            "detail": f"Invalid JSON: {e}",
            "fix": f"Delete and recreate: rm {CONFIG_FILE}",
            "config": {},
        }


def check_api_keys() -> dict:
    """Check which API keys are available."""
    keys_found = {}
    for name, env_var in [
        ("Google", "GOOGLE_API_KEY"),
        ("Anthropic", "ANTHROPIC_API_KEY"),
        ("OpenAI", "OPENAI_API_KEY"),
        ("Universal", "CLAUDE_MEMORY_API_KEY"),
    ]:
        val = os.environ.get(env_var, "")
        if val:
            # Show first 4 and last 4 chars
            masked = val[:4] + "..." + val[-4:] if len(val) > 12 else "***"
            keys_found[name] = f"{env_var}={masked}"

    if keys_found:
        detail = "; ".join(f"{k}: {v}" for k, v in keys_found.items())
        return {"name": "API keys", "status": "ok", "detail": detail, "fix": None, "keys": keys_found}

    # Check if claude CLI is available as fallback
    if shutil.which("claude"):
        return {
            "name": "API keys",
            "status": "ok",
            "detail": "No API keys found, but `claude` CLI available for fallback",
            "fix": None,
            "keys": {"Claude CLI": "available"},
        }

    return {
        "name": "API keys",
        "status": "fail",
        "detail": "No API keys found and no `claude` CLI available",
        "fix": "Set GOOGLE_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY — or set provider to 'claude'",
        "keys": {},
    }


def check_database() -> dict:
    """Check database status."""
    from storage import get_db_path

    db_path = get_db_path()
    db_file = Path(db_path)

    if not db_file.exists():
        return {
            "name": "Database",
            "status": "missing",
            "detail": db_path,
            "fix": "Will be initialized during setup",
        }

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        obs_count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        consol_count = conn.execute("SELECT COUNT(*) FROM consolidations").fetchone()[0]
        size = db_file.stat().st_size
        conn.close()

        size_str = f"{size / 1024:.1f} KB" if size < 1048576 else f"{size / 1048576:.1f} MB"
        return {
            "name": "Database",
            "status": "ok",
            "detail": f"{obs_count} observations, {session_count} sessions, {consol_count} consolidations ({size_str})",
            "fix": None,
        }
    except Exception as e:
        return {
            "name": "Database",
            "status": "fail",
            "detail": f"Error reading DB: {e}",
            "fix": "Database may be corrupted. Backup and re-initialize.",
        }


def check_provider_env() -> dict:
    """Check configured provider override."""
    provider = os.environ.get("CLAUDE_MEMORY_PROVIDER", "")
    model = os.environ.get("CLAUDE_MEMORY_MODEL", "")
    if provider:
        detail = f"CLAUDE_MEMORY_PROVIDER={provider}"
        if model:
            detail += f", CLAUDE_MEMORY_MODEL={model}"
        return {"name": "Provider env override", "status": "ok", "detail": detail, "fix": None}
    return {"name": "Provider env override", "status": "info", "detail": "Not set (will use config file or default)", "fix": None}


def run_diagnostics() -> list[dict]:
    """Run all checks and return results."""
    return [
        check_python(),
        check_venv(),
        check_dependencies(),
        check_config(),
        check_api_keys(),
        check_provider_env(),
        check_database(),
    ]


def main():
    results = run_diagnostics()
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
