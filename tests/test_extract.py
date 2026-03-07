"""Tests for the extraction module with mock LLM."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from extract import (
    parse_session_log,
    parse_transcript,
    parse_llm_response,
    truncate_to_tokens,
    run_extraction,
    acquire_lock,
    release_lock,
)
from storage import get_observations, init_db


MOCK_LLM_RESPONSE = json.dumps([
    {
        "content": "Auth service uses JWT with RS256 signing for API tokens",
        "entities": ["src/auth.py", "JWT", "RS256"],
        "topics": ["authentication", "security"],
        "priority": "P1",
        "importance": 0.9,
    },
    {
        "content": "Database connection pool max size is 20, configured in config.py",
        "entities": ["config.py", "PostgreSQL"],
        "topics": ["database", "configuration"],
        "priority": "P3",
        "importance": 0.5,
    },
])


def test_transcript_parsing(tmp_path):
    """Verify correct filtering and summarization of transcript entries."""
    transcript = tmp_path / "transcript.jsonl"
    entries = [
        {"type": "user", "content": "Fix the auth bug"},
        {"type": "thinking", "content": "Let me think about this..."},
        {"type": "assistant", "content": "I'll look at the auth module."},
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/src/auth.py"}},
        {"type": "tool_result", "content": "def login(): pass"},
    ]
    transcript.write_text("\n".join(json.dumps(e) for e in entries))

    result = parse_transcript(str(transcript))
    assert "Fix the auth bug" in result
    assert "thinking" not in result.lower() or "Let me think" not in result
    assert "Read" in result
    assert "login" in result


def test_transcript_parsing_empty(tmp_path):
    """Empty transcript returns empty string."""
    assert parse_transcript("") == ""
    assert parse_transcript("/nonexistent/path") == ""


def test_session_log_parsing(tmp_path):
    """Parse session JSONL events."""
    log = tmp_path / "session.jsonl"
    events = [
        {"timestamp": "2026-03-06T10:00:00Z", "event_type": "tool_use", "tool_name": "Edit", "tool_input_summary": "Edit main.py"},
        {"timestamp": "2026-03-06T10:01:00Z", "event_type": "pre_compact", "context_summary": "Working on auth"},
        {"timestamp": "2026-03-06T10:02:00Z", "event_type": "session_end"},
    ]
    log.write_text("\n".join(json.dumps(e) for e in events))

    result = parse_session_log(str(log))
    assert "Edit" in result
    assert "main.py" in result
    assert "compact" in result
    assert "session ended" in result


def test_extraction_stores_observations(tmp_path):
    """Mock LLM returns JSON, verify observations stored in DB."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    session_log = tmp_path / "sessions" / "test-session.jsonl"
    session_log.parent.mkdir(parents=True)
    session_log.write_text(json.dumps({
        "timestamp": "2026-03-06T10:00:00Z",
        "event_type": "tool_use",
        "tool_name": "Edit",
        "tool_input_summary": "Edit auth.py",
    }))

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({"type": "user", "content": "Fix the authentication module"}))

    mock_provider = MagicMock()
    mock_provider.complete.return_value = MOCK_LLM_RESPONSE

    with patch("extract.get_provider", return_value=mock_provider), \
         patch("extract.get_db_path", return_value=db_path), \
         patch("extract.get_memory_dir", return_value=str(tmp_path)):
        run_extraction("test-session", str(transcript))

    obs = get_observations(db_path=db_path)
    assert len(obs) == 2
    assert any("JWT" in o["content"] for o in obs)
    assert any(o["priority"] == 1 for o in obs)


def test_extraction_max_20(tmp_path):
    """LLM returns 25 observations, verify only 20 stored."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    session_log = tmp_path / "sessions" / "test-max.jsonl"
    session_log.parent.mkdir(parents=True)
    session_log.write_text(json.dumps({
        "timestamp": "2026-03-06T10:00:00Z", "event_type": "tool_use",
        "tool_name": "Edit", "tool_input_summary": "Edit stuff",
    }))

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({"type": "user", "content": "Lots of work done"}))

    observations_25 = [
        {"content": f"Observation {i}", "entities": [], "topics": ["test"], "priority": "P3", "importance": 0.5}
        for i in range(25)
    ]

    mock_provider = MagicMock()
    mock_provider.complete.return_value = json.dumps(observations_25)

    with patch("extract.get_provider", return_value=mock_provider), \
         patch("extract.get_db_path", return_value=db_path), \
         patch("extract.get_memory_dir", return_value=str(tmp_path)):
        run_extraction("test-max", str(transcript))

    obs = get_observations(db_path=db_path)
    assert len(obs) == 20


def test_extraction_lock_file(tmp_path):
    """Verify lock prevents concurrent runs."""
    lock_path = tmp_path / "extract.lock"

    assert acquire_lock(lock_path) is True
    assert acquire_lock(lock_path) is False  # Second attempt should fail

    release_lock(lock_path)
    assert acquire_lock(lock_path) is True  # Should work after release
    release_lock(lock_path)


def test_extraction_stale_lock(tmp_path):
    """Verify stale lock with dead PID is cleaned up."""
    lock_path = tmp_path / "extract.lock"
    # Write a lock with a dead PID and old timestamp
    lock_path.write_text(json.dumps({"pid": 999999999, "timestamp": time.time() - 700}))

    assert acquire_lock(lock_path) is True  # Should acquire (stale lock)
    release_lock(lock_path)


def test_extraction_graceful_failure(tmp_path):
    """LLM returns garbage, verify no crash, no data corruption."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    session_log = tmp_path / "sessions" / "test-fail.jsonl"
    session_log.parent.mkdir(parents=True)
    session_log.write_text(json.dumps({
        "timestamp": "2026-03-06T10:00:00Z", "event_type": "tool_use",
        "tool_name": "Edit", "tool_input_summary": "Edit stuff",
    }))

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({"type": "user", "content": "Do some work"}))

    mock_provider = MagicMock()
    mock_provider.complete.return_value = "This is not valid JSON at all!!!"

    with patch("extract.get_provider", return_value=mock_provider), \
         patch("extract.get_db_path", return_value=db_path), \
         patch("extract.get_memory_dir", return_value=str(tmp_path)):
        # Should not raise
        run_extraction("test-fail", str(transcript))

    obs = get_observations(db_path=db_path)
    assert len(obs) == 0  # No observations stored, but no crash


def test_provider_factory():
    """Verify get_provider() returns correct class based on env var."""
    from llm_provider import get_provider, AnthropicProvider, OpenAIProvider

    with patch.dict(os.environ, {"CLAUDE_MEMORY_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "test-key"}):
        provider = get_provider()
        assert isinstance(provider, AnthropicProvider)

    with patch.dict(os.environ, {"CLAUDE_MEMORY_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"}):
        provider = get_provider()
        assert isinstance(provider, OpenAIProvider)


def test_parse_llm_response_with_markdown():
    """Parse response wrapped in markdown code fences."""
    response = '```json\n[{"content": "test", "entities": [], "topics": [], "priority": "P3", "importance": 0.5}]\n```'
    result = parse_llm_response(response)
    assert len(result) == 1
    assert result[0]["content"] == "test"


def test_truncate_to_tokens():
    """Verify truncation keeps recent content."""
    short_text = "short"
    assert truncate_to_tokens(short_text, 100) == short_text

    long_text = "A" * 40000  # ~10K tokens
    result = truncate_to_tokens(long_text, 8000)
    assert len(result) < len(long_text)
    assert result.startswith("...[truncated]")
