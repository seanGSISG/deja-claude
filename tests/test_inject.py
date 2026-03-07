"""Tests for context injection (query + inject modules)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from inject import build_injection_context, estimate_tokens, format_injection
from query import gather_context_signals, query_memories, relevance_score
from storage import (
    get_db_path,
    init_db,
    store_consolidation,
    store_observation,
    store_session,
)


def test_relevance_scoring(populated_db):
    """Verify scoring formula with known inputs."""
    from storage import get_observations

    obs = get_observations(db_path=populated_db)
    assert len(obs) > 0

    context = {"active_topics": {"authentication", "security"}, "branch": "main"}

    # P1 observation about auth should score highest
    scores = [(relevance_score(o, context), o) for o in obs]
    scores.sort(key=lambda x: x[0], reverse=True)

    best = scores[0][1]
    assert best["priority"] == 1
    assert "JWT" in best["content"]


def test_relevance_score_no_topics():
    """Score without topic overlap still works."""
    obs = {"created_at": "2026-03-06T00:00:00+00:00", "priority": 2, "importance": 0.7, "topics": ["auth"]}
    context = {"active_topics": set()}
    score = relevance_score(obs, context)
    assert 0 < score < 1


def test_format_injection_token_budget(populated_db):
    """Verify output stays within token budget."""
    # Add many observations to fill budget
    for i in range(30):
        store_observation(
            session_id="session-1",
            content=f"Observation {i}: " + "x" * 100,
            entities=[],
            topics=["test"],
            priority=3,
            importance=0.5,
            db_path=populated_db,
        )

    context = {"active_topics": {"test"}, "branch": "main"}
    results = query_memories(db_path=populated_db, context_signals=context)
    output = format_injection(results, max_tokens=512)

    assert estimate_tokens(output) <= 600  # Allow some slack for headers/footer


def test_format_injection_section_order(populated_db):
    """Verify consolidation insights appear first."""
    store_consolidation(
        source_ids=[],
        summary="Auth and caching are related",
        insight="JWT tokens and Redis cache share the same TTL strategy",
        connections=[],
        db_path=populated_db,
    )

    context = {"active_topics": set(), "branch": "main"}
    results = query_memories(db_path=populated_db, context_signals=context)
    output = format_injection(results)

    if "Key Insights" in output and "Known Issues" in output:
        assert output.index("Key Insights") < output.index("Known Issues")
    elif "Key Insights" in output and "Recent Decisions" in output:
        assert output.index("Key Insights") < output.index("Recent Decisions")


def test_format_injection_deduplication(populated_db):
    """Same observation doesn't appear twice."""
    context = {"active_topics": {"authentication", "security"}, "branch": "main"}
    results = query_memories(db_path=populated_db, context_signals=context)
    output = format_injection(results)

    # Count occurrences of the JWT observation
    count = output.count("JWT tokens with RS256")
    assert count <= 1


def test_empty_db_graceful(tmp_path):
    """Empty DB returns empty string, no error."""
    db_path = str(tmp_path / "empty.db")
    init_db(db_path)

    result = build_injection_context(db_path=db_path)
    assert result == ""


def test_missing_db_graceful(tmp_path):
    """Missing DB returns empty string, no error."""
    db_path = str(tmp_path / "nonexistent.db")
    result = build_injection_context(db_path=db_path)
    assert result == ""


def test_context_signals_git():
    """Verify git branch/recent files extraction (may be empty in test env)."""
    signals = gather_context_signals()
    assert "branch" in signals
    assert "working_dir" in signals
    assert "recent_files" in signals
    assert "active_topics" in signals
    assert isinstance(signals["active_topics"], set)


def test_build_injection_under_500ms(populated_db):
    """Performance test with populated DB."""
    start = time.time()
    build_injection_context(db_path=populated_db)
    elapsed_ms = (time.time() - start) * 1000
    assert elapsed_ms < 500, f"Injection took {elapsed_ms:.0f}ms, exceeds 500ms budget"


def test_query_memories_returns_scored(populated_db):
    """Query returns results with _score and _type."""
    context = {"active_topics": set(), "branch": "main"}
    results = query_memories(db_path=populated_db, context_signals=context)
    assert len(results) > 0
    for r in results:
        assert "_score" in r
        assert "_type" in r
        assert r["_score"] >= 0
