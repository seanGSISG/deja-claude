"""Write claude-memory config file.

Usage:
    python write_config.py --provider google --model gemini-3.1-flash-lite-preview
    python write_config.py --provider claude --fallback-to-claude true
    python write_config.py --provider anthropic --retention-days 60 --max-inject-tokens 8192
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "claude-memory"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_MODELS = {
    "google": "gemini-3.1-flash-lite-preview",
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
    "local": "default",
    "claude": "haiku",
}


def write_config(
    provider: str,
    model: str | None = None,
    fallback_to_claude: bool = True,
    retention_days: int = 30,
    max_inject_tokens: int = 4096,
    local_url: str | None = None,
) -> dict:
    """Write config and return the config dict."""
    config: dict = {
        "provider": provider,
        "model": model or DEFAULT_MODELS.get(provider, ""),
        "fallback_to_claude": fallback_to_claude,
        "retention_days": retention_days,
        "max_inject_tokens": max_inject_tokens,
    }

    if provider == "local" and local_url:
        config["local_url"] = local_url

    if provider == "claude":
        config["claude_model"] = model or "haiku"

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Merge with existing config to preserve any extra fields
    existing = {}
    if CONFIG_FILE.exists():
        try:
            existing = json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    existing.update(config)
    CONFIG_FILE.write_text(json.dumps(existing, indent=2) + "\n")
    return existing


def main():
    parser = argparse.ArgumentParser(description="Write claude-memory config")
    parser.add_argument("--provider", required=True, choices=["google", "anthropic", "openai", "local", "claude"])
    parser.add_argument("--model", default=None, help="Model name (uses provider default if omitted)")
    parser.add_argument("--fallback-to-claude", default="true", help="Fall back to claude -p if no API key (true/false)")
    parser.add_argument("--retention-days", type=int, default=30, help="Auto-prune threshold in days")
    parser.add_argument("--max-inject-tokens", type=int, default=4096, help="Max tokens injected at session start")
    parser.add_argument("--local-url", default=None, help="URL for local OpenAI-compatible endpoint")
    args = parser.parse_args()

    config = write_config(
        provider=args.provider,
        model=args.model,
        fallback_to_claude=args.fallback_to_claude.lower() == "true",
        retention_days=args.retention_days,
        max_inject_tokens=args.max_inject_tokens,
        local_url=args.local_url,
    )
    print(json.dumps(config, indent=2))


if __name__ == "__main__":
    main()
