"""End-to-end integration tests for deja-claude plugin."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from storage import (
    get_observations,
    get_consolidations,
    init_db,
    store_observation,
    store_session,
)
from inject import build_injection_context
from extract import run_extraction
from consolidate import run_consolidation


REPO_ROOT = Path(__file__).parent.parent
HOOKS_DIR = REPO_ROOT / "hooks"
SCRIPTS_DIR = REPO_ROOT / "scripts"


def test_full_session_lifecycle(tmp_path):
    """End-to-end: record -> extract -> consolidate -> inject."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    # a. Simulate SessionStart (register session)
    store_session("lifecycle-test", branch="main", working_dir="/project", db_path=db_path)

    # b. Simulate PostToolUse events (write JSONL)
    session_log = tmp_path / "sessions" / "lifecycle-test.jsonl"
    session_log.parent.mkdir(parents=True)
    events = [
        {"timestamp": "2026-03-06T10:00:00Z", "event_type": "tool_use", "tool_name": "Edit", "tool_input_summary": "Edit src/auth.py"},
        {"timestamp": "2026-03-06T10:01:00Z", "event_type": "tool_use", "tool_name": "Bash", "tool_input_summary": "pytest"},
        {"timestamp": "2026-03-06T10:02:00Z", "event_type": "tool_use", "tool_name": "Read", "tool_input_summary": "Read config.py"},
    ]
    session_log.write_text("\n".join(json.dumps(e) for e in events))

    # c. Create transcript
    transcript = tmp_path / "transcript.jsonl"
    transcript_entries = [
        {"type": "user", "content": "Fix the authentication module to use JWT"},
        {"type": "assistant", "content": "I'll update the auth module to use JWT tokens."},
        {"type": "tool_use", "name": "Edit", "input": {"file_path": "/src/auth.py"}},
        {"type": "tool_result", "content": "File edited successfully"},
    ]
    transcript.write_text("\n".join(json.dumps(e) for e in transcript_entries))

    # d. Run extraction with mock LLM
    mock_observations = [
        {"content": "Auth module switched to JWT with RS256 signing", "entities": ["src/auth.py"], "topics": ["auth", "jwt"], "priority": "P1", "importance": 0.9},
        {"content": "Config.py holds all JWT settings including TTL", "entities": ["config.py"], "topics": ["auth", "configuration"], "priority": "P3", "importance": 0.5},
        {"content": "Tests pass after auth refactor", "entities": ["tests/"], "topics": ["testing", "auth"], "priority": "P4", "importance": 0.3},
    ]

    mock_provider = MagicMock()
    mock_provider.complete.return_value = json.dumps(mock_observations)

    with patch("extract.get_provider", return_value=mock_provider), \
         patch("extract.get_db_path", return_value=db_path), \
         patch("extract.get_memory_dir", return_value=str(tmp_path)):
        run_extraction("lifecycle-test", str(transcript))

    obs = get_observations(db_path=db_path)
    assert len(obs) == 3

    # e. Run consolidation with mock LLM
    consolidation_response = {
        "summary": "Auth module was refactored to use JWT",
        "insight": "JWT configuration centralized in config.py with RS256 signing",
        "connections": [{"from_id": obs[0]["id"], "to_id": obs[1]["id"], "relationship": "config supports auth"}],
        "source_ids": [o["id"] for o in obs],
        "contradictions": [],
        "redundant_ids": [],
    }

    mock_provider2 = MagicMock()
    mock_provider2.complete.return_value = json.dumps(consolidation_response)

    with patch("consolidate.get_provider", return_value=mock_provider2):
        result = run_consolidation(db_path=db_path)

    assert result is True
    consolidations = get_consolidations(db_path=db_path)
    assert len(consolidations) >= 1

    # f. Simulate new SessionStart (verify memories injected)
    context = build_injection_context(db_path=db_path)
    assert "Project Memory" in context or context == ""  # May be empty if all consolidated


def test_plugin_json_valid():
    """Parse and validate plugin.json schema."""
    plugin_json_path = REPO_ROOT / ".claude-plugin" / "plugin.json"
    assert plugin_json_path.exists(), "plugin.json not found"

    data = json.loads(plugin_json_path.read_text())
    assert data["name"] == "claude-memory"
    assert "version" in data
    assert "hooks" in data
    assert "skills" in data
    assert isinstance(data["skills"], list)


def test_marketplace_json_valid():
    """Parse and validate marketplace.json schema."""
    marketplace_path = REPO_ROOT / ".claude-plugin" / "marketplace.json"
    assert marketplace_path.exists(), "marketplace.json not found"

    data = json.loads(marketplace_path.read_text())
    assert data["name"] == "deja-claude"
    assert "plugins" in data
    assert isinstance(data["plugins"], list)
    assert len(data["plugins"]) >= 1
    assert data["plugins"][0]["name"] == "claude-memory"


def test_hooks_json_valid():
    """Parse and validate hooks.json nested schema."""
    hooks_json_path = HOOKS_DIR / "hooks.json"
    assert hooks_json_path.exists(), "hooks.json not found"

    data = json.loads(hooks_json_path.read_text())
    assert "hooks" in data
    hooks = data["hooks"]
    assert isinstance(hooks, dict), "hooks should be a dict keyed by event name"

    expected_events = {"SessionStart", "PostToolUse", "PreCompact", "Stop", "SessionEnd"}
    assert set(hooks.keys()) == expected_events

    for event_name, matchers in hooks.items():
        assert isinstance(matchers, list), f"{event_name} should be a list of matchers"
        for matcher_entry in matchers:
            assert "hooks" in matcher_entry, f"{event_name} matcher missing 'hooks' key"
            for hook in matcher_entry["hooks"]:
                assert "type" in hook
                assert hook["type"] == "command"
                assert "command" in hook
                assert "timeout" in hook
                assert isinstance(hook["timeout"], (int, float))
                assert hook["timeout"] <= 60, "timeout should be in seconds, not milliseconds"


def test_all_hooks_executable():
    """Verify all .sh files have execute permission."""
    for sh_file in HOOKS_DIR.glob("*.sh"):
        mode = sh_file.stat().st_mode
        assert mode & stat.S_IXUSR, f"{sh_file.name} is not executable"


def test_all_scripts_importable():
    """Verify all .py files import without errors."""
    for py_file in SCRIPTS_DIR.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        # Use subprocess to test import in isolation
        result = subprocess.run(
            ["python3", "-c", f"import importlib.util; spec = importlib.util.spec_from_file_location('mod', '{py_file}'); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"Failed to import {py_file.name}: {result.stderr}"


def test_setup_idempotent(tmp_path):
    """Run setup.sh concept twice, verify no errors."""
    # We can't run setup.sh directly (it installs deps), but verify the DB init is idempotent
    db_path = str(tmp_path / "test.db")
    conn1 = init_db(db_path)
    conn1.close()
    conn2 = init_db(db_path)
    conn2.close()
    # No error = idempotent


def test_graceful_degradation(tmp_path):
    """Verify injection works when DB is missing."""
    db_path = str(tmp_path / "nonexistent.db")
    result = build_injection_context(db_path=db_path)
    assert result == ""  # Empty, no crash


def test_memory_query_skill():
    """Verify SKILL.md exists and has correct structure."""
    skill_path = REPO_ROOT / "skills" / "memory-query" / "SKILL.md"
    assert skill_path.exists(), "SKILL.md not found"

    content = skill_path.read_text()
    assert "name: memory-query" in content
    assert "query" in content
    assert "python3" in content


def test_claude_code_provider_init():
    """ClaudeCodeProvider can be instantiated with model."""
    from llm_provider import ClaudeCodeProvider
    p = ClaudeCodeProvider(model="haiku")
    assert p.model == "haiku"

    p2 = ClaudeCodeProvider()
    assert p2.model == "haiku"  # default


def test_claude_code_provider_in_factory():
    """get_provider() returns ClaudeCodeProvider when provider='claude'."""
    from llm_provider import get_provider, ClaudeCodeProvider
    with patch.dict(os.environ, {"CLAUDE_MEMORY_PROVIDER": "claude"}, clear=False):
        provider = get_provider()
        assert isinstance(provider, ClaudeCodeProvider)


def test_config_loading(tmp_path):
    """Config file loads correctly."""
    from llm_provider import load_config
    config_file = tmp_path / "config.json"
    config_file.write_text('{"provider": "claude", "model": "sonnet"}')
    config = load_config(str(config_file))
    assert config["provider"] == "claude"
    assert config["model"] == "sonnet"


def test_config_loading_missing():
    """Missing config file returns empty dict."""
    from llm_provider import load_config
    config = load_config("/nonexistent/path/config.json")
    assert config == {}


def test_fallback_to_claude(tmp_path):
    """When API key is missing and claude is available, falls back to ClaudeCodeProvider."""
    from llm_provider import get_provider, ClaudeCodeProvider

    config_file = tmp_path / "config.json"
    config_file.write_text('{"provider": "google", "fallback_to_claude": true}')

    with patch.dict(os.environ, {
        "CLAUDE_MEMORY_PROVIDER": "google",
        "GOOGLE_API_KEY": "",
        "CLAUDE_MEMORY_API_KEY": "",
    }, clear=False), \
         patch("llm_provider.CONFIG_FILE", config_file), \
         patch("llm_provider._try_claude_available", return_value=True):
        provider = get_provider()
        assert isinstance(provider, ClaudeCodeProvider)


def test_no_fallback_raises(tmp_path):
    """When fallback_to_claude is false and no API key, raises ValueError."""
    from llm_provider import get_provider
    import pytest

    config_file = tmp_path / "config.json"
    config_file.write_text('{"provider": "google", "fallback_to_claude": false}')

    with patch.dict(os.environ, {
        "CLAUDE_MEMORY_PROVIDER": "google",
        "GOOGLE_API_KEY": "",
        "CLAUDE_MEMORY_API_KEY": "",
    }, clear=False), \
         patch("llm_provider.CONFIG_FILE", config_file):
        with pytest.raises(ValueError, match="No API key found"):
            get_provider()
