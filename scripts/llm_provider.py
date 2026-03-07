"""Multi-provider LLM abstraction for claude-memory plugin.

Supports Google (Gemini), Anthropic, OpenAI, local OpenAI-compatible endpoints,
and Claude Code pipe mode (claude -p).
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path

DEFAULT_TIMEOUT = 60
CONFIG_DIR = Path.home() / ".config" / "claude-memory"
CONFIG_FILE = CONFIG_DIR / "config.json"


class LLMProvider(ABC):
    """Base class for LLM providers."""

    @abstractmethod
    def complete(self, system_prompt: str, user_message: str) -> str:
        """Send a completion request and return the text response."""


class GoogleProvider(LLMProvider):
    """Google Gemini provider via google-genai SDK."""

    def __init__(self, model: str, api_key: str):
        try:
            from google import genai
        except ImportError:
            raise ImportError(
                "google-genai package required for Google provider. "
                "Install with: pip install google-genai"
            )
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def complete(self, system_prompt: str, user_message: str) -> str:
        from google.genai import types

        response = self.client.models.generate_content(
            model=self.model,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.3,
            ),
        )
        return response.text


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider."""

    def __init__(self, model: str, api_key: str):
        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "anthropic package required for Anthropic provider. "
                "Install with: pip install anthropic"
            )
        self.client = anthropic.Anthropic(api_key=api_key, timeout=DEFAULT_TIMEOUT)
        self.model = model

    def complete(self, system_prompt: str, user_message: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text


class OpenAIProvider(LLMProvider):
    """OpenAI provider."""

    def __init__(self, model: str, api_key: str, base_url: str | None = None):
        try:
            import openai
        except ImportError:
            raise ImportError(
                "openai package required for OpenAI provider. "
                "Install with: pip install openai"
            )
        kwargs = {"api_key": api_key, "timeout": DEFAULT_TIMEOUT}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = openai.OpenAI(**kwargs)
        self.model = model

    def complete(self, system_prompt: str, user_message: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,
        )
        return response.choices[0].message.content


class LocalProvider(OpenAIProvider):
    """OpenAI-compatible local endpoint."""

    def __init__(self, model: str, base_url: str):
        super().__init__(model=model, api_key="local", base_url=base_url)


class ClaudeCodeProvider(LLMProvider):
    """Claude Code pipe mode provider — uses the user's Anthropic subscription via `claude -p`."""

    def __init__(self, model: str = "haiku"):
        self.model = model

    def complete(self, system_prompt: str, user_message: str) -> str:
        import subprocess

        env = os.environ.copy()
        # Remove CLAUDECODE to avoid nested session error
        env.pop("CLAUDECODE", None)

        result = subprocess.run(
            [
                "claude", "-p",
                "--model", self.model,
                "--system-prompt", system_prompt,
                "--no-session-persistence",
            ],
            input=user_message,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude -p failed: {result.stderr}")
        return result.stdout.strip()


# Default models per provider
DEFAULT_MODELS = {
    "google": "gemini-3.1-flash-lite-preview",
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
    "local": "default",
    "claude": "haiku",
}

# API key env var fallbacks per provider
API_KEY_FALLBACKS = {
    "google": "GOOGLE_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

# Config file key names for API key env var overrides
API_KEY_CONFIG_FIELDS = {
    "google": "google_api_key_env",
    "anthropic": "anthropic_api_key_env",
    "openai": "openai_api_key_env",
}


def load_config(config_path: str | None = None) -> dict:
    """Load config from file, returning empty dict if missing."""
    path = Path(config_path) if config_path else CONFIG_FILE
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _resolve_api_key(provider_name: str, config: dict) -> str:
    """Resolve API key from env vars, with config-specified env var names."""
    # Priority 1: Universal override
    api_key = os.environ.get("CLAUDE_MEMORY_API_KEY", "")
    if api_key:
        return api_key

    # Priority 2: Config-specified env var name
    config_field = API_KEY_CONFIG_FIELDS.get(provider_name, "")
    if config_field:
        env_var_name = config.get(config_field, API_KEY_FALLBACKS.get(provider_name, ""))
        if env_var_name:
            api_key = os.environ.get(env_var_name, "")
            if api_key:
                return api_key

    # Priority 3: Default env var fallback
    fallback_var = API_KEY_FALLBACKS.get(provider_name, "")
    if fallback_var:
        return os.environ.get(fallback_var, "")

    return ""


def _try_claude_available() -> bool:
    """Check if `claude` CLI is available."""
    import shutil
    return shutil.which("claude") is not None


def get_provider(provider_name: str | None = None) -> LLMProvider:
    """Factory: select provider based on env vars, config file, or argument.

    Auto-fallback chain:
    1. Env var overrides (CLAUDE_MEMORY_PROVIDER, CLAUDE_MEMORY_MODEL)
    2. Config file (~/.config/claude-memory/config.json)
    3. If API key missing and fallback_to_claude is true, use claude -p
    """
    config = load_config()

    # Resolve provider name: env var > argument > config > default
    name = os.environ.get("CLAUDE_MEMORY_PROVIDER", "")
    if not name:
        name = provider_name or config.get("provider", "google")

    # Resolve model: env var > config > default
    model = os.environ.get("CLAUDE_MEMORY_MODEL", "")
    if not model:
        model = config.get("model", "") if config.get("provider", "") == name else ""
    if not model:
        model = DEFAULT_MODELS.get(name, "")

    # Claude provider doesn't need an API key
    if name == "claude":
        claude_model = model or config.get("claude_model", "haiku")
        return ClaudeCodeProvider(model=claude_model)

    # Local provider doesn't need an API key
    if name == "local":
        base_url = os.environ.get("CLAUDE_MEMORY_LOCAL_URL", "http://localhost:8080")
        return LocalProvider(model=model, base_url=base_url)

    # Resolve API key
    api_key = _resolve_api_key(name, config)

    # If no API key, try fallback to claude -p
    if not api_key:
        fallback = config.get("fallback_to_claude", True)
        if fallback and _try_claude_available():
            claude_model = config.get("claude_model", "haiku")
            return ClaudeCodeProvider(model=claude_model)
        raise ValueError(
            f"No API key found for provider '{name}'. "
            f"Set {API_KEY_FALLBACKS.get(name, 'CLAUDE_MEMORY_API_KEY')} env var, "
            f"or set provider to 'claude' to use Claude Code pipe mode."
        )

    if name == "google":
        return GoogleProvider(model=model, api_key=api_key)
    elif name == "anthropic":
        return AnthropicProvider(model=model, api_key=api_key)
    elif name == "openai":
        return OpenAIProvider(model=model, api_key=api_key)
    else:
        raise ValueError(f"Unknown LLM provider: {name}. Use: google, anthropic, openai, local, claude")
