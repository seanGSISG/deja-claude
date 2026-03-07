"""Tests for the consolidation module with mock LLM."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from consolidate import (
    acquire_lock,
    parse_consolidation_response,
    release_lock,
    run_consolidation,
    run_with_lock,
)
from storage import (
    get_connection,
    get_consolidations,
    get_observations,
    init_db,
    store_observation,
    store_session,
)


MOCK_CONSOLIDATION_RESPONSE = json.dumps({
    "summary": "Auth and caching modules share similar timeout patterns",
    "insight": "Both JWT token refresh and Redis cache TTL use 5-minute windows",
    "connections": [
        {"from_id": 1, "to_id": 2, "relationship": "both use 5-minute timeout patterns"},
    ],
    "source_ids": [1, 2, 3],
    "contradictions": [],
    "redundant_ids": [],
})


def _populate_for_consolidation(db_path: str, count: int = 5) -> list[int]:
    """Insert test observations and return their IDs."""
    store_session("test-session", branch="main", working_dir="/project", db_path=db_path)
    ids = []
    for i in range(count):
        oid = store_observation(
            session_id="test-session",
            content=f"Observation {i}: test content about feature {i}",
            entities=[f"module_{i}"],
            topics=["testing", f"feature_{i}"],
            priority=2,
            importance=0.7,
            db_path=db_path,
        )
        ids.append(oid)
    return ids


def test_consolidation_threshold(tmp_path):
    """< 3 observations = skip, >= 3 = process."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    store_session("s1", db_path=db_path)

    # Only 2 observations - should skip
    store_observation("s1", "obs 1", [], [], 3, 0.5, db_path=db_path)
    store_observation("s1", "obs 2", [], [], 3, 0.5, db_path=db_path)

    result = run_consolidation(db_path=db_path)
    assert result is False


def test_consolidation_stores_result(tmp_path):
    """Mock LLM response, verify consolidation stored in DB."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    ids = _populate_for_consolidation(db_path, count=5)

    # Build mock response with valid source_ids
    response = {
        "summary": "Test observations share common patterns",
        "insight": "Features 0-4 follow the same module structure",
        "connections": [{"from_id": ids[0], "to_id": ids[1], "relationship": "related modules"}],
        "source_ids": ids[:3],
        "contradictions": [],
        "redundant_ids": [],
    }

    mock_provider = MagicMock()
    mock_provider.complete.return_value = json.dumps(response)

    with patch("consolidate.get_provider", return_value=mock_provider):
        result = run_consolidation(db_path=db_path)

    assert result is True
    consolidations = get_consolidations(db_path=db_path)
    assert len(consolidations) >= 1
    assert "common patterns" in consolidations[0]["summary"]


def test_consolidation_marks_sources(tmp_path):
    """Source observations get consolidated=1."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    ids = _populate_for_consolidation(db_path, count=4)

    response = {
        "summary": "Summary", "insight": "Insight",
        "connections": [],
        "source_ids": ids[:3],
        "contradictions": [], "redundant_ids": [],
    }

    mock_provider = MagicMock()
    mock_provider.complete.return_value = json.dumps(response)

    with patch("consolidate.get_provider", return_value=mock_provider):
        run_consolidation(db_path=db_path)

    obs = get_observations(unconsolidated_only=True, db_path=db_path)
    consolidated_ids = {o["id"] for o in get_observations(db_path=db_path) if o["consolidated"]}

    for sid in ids[:3]:
        assert sid in consolidated_ids


def test_consolidation_bidirectional_connections(tmp_path):
    """Connections stored on both sides."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    ids = _populate_for_consolidation(db_path, count=3)

    response = {
        "summary": "Summary", "insight": "Insight",
        "connections": [{"from_id": ids[0], "to_id": ids[1], "relationship": "linked"}],
        "source_ids": ids,
        "contradictions": [], "redundant_ids": [],
    }

    mock_provider = MagicMock()
    mock_provider.complete.return_value = json.dumps(response)

    with patch("consolidate.get_provider", return_value=mock_provider):
        run_consolidation(db_path=db_path)

    # Check both observations have connection info in entities
    conn = get_connection(db_path)
    for oid in [ids[0], ids[1]]:
        row = conn.execute("SELECT entities FROM observations WHERE id = ?", (oid,)).fetchone()
        entities = json.loads(row["entities"])
        link_entries = [e for e in entities if isinstance(e, dict) and "linked_to" in e]
        assert len(link_entries) > 0


def test_contradiction_resolution(tmp_path):
    """Mock contradiction response, verify handling."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    ids = _populate_for_consolidation(db_path, count=3)

    response = {
        "summary": "Updated understanding",
        "insight": "Previous assumption was wrong, new evidence shows different pattern",
        "connections": [],
        "source_ids": ids,
        "contradictions": [
            {"observation_id": ids[0], "contradicts": "Old pattern X", "resolution": "New pattern Y is correct"}
        ],
        "redundant_ids": [],
    }

    mock_provider = MagicMock()
    mock_provider.complete.return_value = json.dumps(response)

    with patch("consolidate.get_provider", return_value=mock_provider):
        result = run_consolidation(db_path=db_path)

    assert result is True
    consolidations = get_consolidations(db_path=db_path)
    assert len(consolidations) >= 1


def test_redundancy_handling(tmp_path):
    """Redundant IDs get importance set to 0.1."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    ids = _populate_for_consolidation(db_path, count=4)

    response = {
        "summary": "Summary", "insight": "Insight",
        "connections": [],
        "source_ids": ids[:3],
        "contradictions": [],
        "redundant_ids": [ids[3]],
    }

    mock_provider = MagicMock()
    mock_provider.complete.return_value = json.dumps(response)

    with patch("consolidate.get_provider", return_value=mock_provider):
        run_consolidation(db_path=db_path)

    conn = get_connection(db_path)
    row = conn.execute("SELECT importance FROM observations WHERE id = ?", (ids[3],)).fetchone()
    assert row["importance"] <= 0.1


def test_decay_importance_p1_slower(tmp_path):
    """P1 observations decay at half rate."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    store_session("s1", db_path=db_path)

    # Insert P1 and P3 observations with same importance
    from datetime import datetime, timezone, timedelta
    conn = get_connection(db_path)

    old_date = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    conn.execute(
        "INSERT INTO observations (session_id, content, entities, topics, priority, importance, created_at, consolidated) VALUES (?,?,?,?,?,?,?,?)",
        ("s1", "P1 obs", "[]", "[]", 1, 0.8, old_date, 0),
    )
    conn.execute(
        "INSERT INTO observations (session_id, content, entities, topics, priority, importance, created_at, consolidated) VALUES (?,?,?,?,?,?,?,?)",
        ("s1", "P3 obs", "[]", "[]", 3, 0.8, old_date, 0),
    )
    conn.commit()

    from storage import decay_importance
    decay_importance(half_life_days=14, db_path=db_path)

    rows = conn.execute("SELECT content, importance FROM observations ORDER BY importance DESC").fetchall()
    p1_imp = next(r["importance"] for r in rows if "P1" in r["content"])
    p3_imp = next(r["importance"] for r in rows if "P3" in r["content"])

    # P1 should have higher importance (slower decay)
    assert p1_imp > p3_imp


def test_pruning_respects_recency(tmp_path):
    """Recent observations never pruned regardless of importance."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    store_session("s1", db_path=db_path)

    # Recent low-importance observation
    store_observation("s1", "Recent but low importance", [], [], 4, 0.1, db_path=db_path)

    from storage import prune_old
    pruned = prune_old(retention_days=30, min_importance=0.3, db_path=db_path)
    assert pruned == 0

    obs = get_observations(db_path=db_path)
    assert len(obs) == 1


def test_lock_prevents_concurrent(tmp_path):
    """Two attempts, only one acquires lock."""
    lock_path = tmp_path / "consolidate.lock"

    assert acquire_lock(lock_path) is True
    assert acquire_lock(lock_path) is False

    release_lock(lock_path)
    assert acquire_lock(lock_path) is True
    release_lock(lock_path)


def test_continuous_mode_concept(tmp_path):
    """Verify the consolidation can run as one-shot."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    # With no data, should just skip gracefully
    with patch("consolidate.get_lock_path", return_value=tmp_path / "test.lock"), \
         patch("consolidate.get_db_path", return_value=db_path):
        result = run_with_lock(db_path=db_path)
    assert result is False  # No data to consolidate


def test_parse_consolidation_response_markdown():
    """Parse response wrapped in markdown code fences."""
    response = '```json\n{"summary": "test", "insight": "insight", "connections": [], "source_ids": [1], "contradictions": [], "redundant_ids": []}\n```'
    result = parse_consolidation_response(response)
    assert result is not None
    assert result["summary"] == "test"


def test_parse_consolidation_response_garbage():
    """Garbage input returns None."""
    assert parse_consolidation_response("not json at all!!!") is None
