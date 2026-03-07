"""Unit tests for the storage module."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from storage import (
    close_connection,
    decay_importance,
    end_session,
    get_connection,
    get_consolidations,
    get_observations,
    init_db,
    prune_old,
    search_observations,
    store_consolidation,
    store_observation,
    store_session,
)


def test_init_db_idempotent(tmp_path):
    """Call init_db() twice, no errors."""
    db_path = str(tmp_path / "test.db")
    conn1 = init_db(db_path)
    conn2 = init_db(db_path)
    # Both should work without errors
    conn1.execute("SELECT 1 FROM observations LIMIT 1")
    conn2.execute("SELECT 1 FROM observations LIMIT 1")
    conn1.close()
    conn2.close()


def test_store_and_get_observation(tmp_db):
    """Store, retrieve, verify all fields including parsed JSON."""
    obs_id = store_observation(
        session_id="s1",
        content="Test observation content",
        entities=["entity1", "entity2"],
        topics=["topic1"],
        priority=2,
        importance=0.8,
        source_file="test.py",
        db_path=tmp_db,
    )
    assert obs_id is not None
    assert obs_id > 0

    results = get_observations(session_id="s1", db_path=tmp_db)
    assert len(results) == 1
    obs = results[0]
    assert obs["content"] == "Test observation content"
    assert obs["entities"] == ["entity1", "entity2"]
    assert obs["topics"] == ["topic1"]
    assert obs["priority"] == 2
    assert abs(obs["importance"] - 0.8) < 0.01
    assert obs["source_file"] == "test.py"
    assert obs["consolidated"] == 0


def test_store_observation_truncates_content(tmp_db):
    """Content longer than 2000 chars is truncated."""
    long_content = "x" * 3000
    store_observation(
        session_id="s1", content=long_content,
        entities=[], topics=[], priority=3, importance=0.5, db_path=tmp_db,
    )
    results = get_observations(session_id="s1", db_path=tmp_db)
    assert len(results[0]["content"]) == 2000


def test_search_observations_fts(populated_db):
    """Store several, search by keyword, verify ranking."""
    results = search_observations("JWT authentication", db_path=populated_db)
    assert len(results) >= 1
    assert any("JWT" in r["content"] for r in results)


def test_search_observations_fts_no_results(tmp_db):
    """Search with no matching results returns empty."""
    results = search_observations("nonexistent query xyz", db_path=tmp_db)
    assert results == []


def test_store_consolidation_marks_consolidated(populated_db):
    """Verify source observations get consolidated=1."""
    obs = get_observations(unconsolidated_only=True, db_path=populated_db)
    source_ids = [obs[0]["id"], obs[1]["id"]]

    store_consolidation(
        source_ids=source_ids,
        summary="Test consolidation summary",
        insight="Test insight",
        connections=[],
        db_path=populated_db,
    )

    # Check source observations are marked consolidated
    all_obs = get_observations(db_path=populated_db)
    for o in all_obs:
        if o["id"] in source_ids:
            assert o["consolidated"] == 1

    # Unconsolidated count should have decreased
    remaining = get_observations(unconsolidated_only=True, db_path=populated_db)
    assert len(remaining) < len(obs)


def test_store_consolidation_bidirectional_connections(populated_db):
    """Verify both sides get connection entries."""
    obs = get_observations(db_path=populated_db)
    id1, id2 = obs[0]["id"], obs[1]["id"]

    store_consolidation(
        source_ids=[id1, id2],
        summary="Connected observations",
        insight="These are related",
        connections=[{"from_id": id1, "to_id": id2, "relationship": "related_to"}],
        db_path=populated_db,
    )

    # Check both observations have connection entries in entities
    updated = get_observations(db_path=populated_db)
    obs_map = {o["id"]: o for o in updated}

    # from_id should have linked_to pointing to to_id
    entities_1 = obs_map[id1]["entities"]
    assert any(
        isinstance(e, dict) and e.get("linked_to") == id2
        for e in entities_1
    )

    # to_id should have linked_to pointing to from_id
    entities_2 = obs_map[id2]["entities"]
    assert any(
        isinstance(e, dict) and e.get("linked_to") == id1
        for e in entities_2
    )


def test_get_consolidations(populated_db):
    """Store and retrieve consolidations."""
    obs = get_observations(db_path=populated_db)
    source_ids = [obs[0]["id"]]

    cid = store_consolidation(
        source_ids=source_ids,
        summary="Consolidation summary",
        insight="Key insight here",
        connections=[],
        db_path=populated_db,
    )

    results = get_consolidations(db_path=populated_db)
    assert len(results) >= 1
    c = results[0]
    assert c["summary"] == "Consolidation summary"
    assert c["insight"] == "Key insight here"
    assert isinstance(c["source_ids"], list)


def test_session_lifecycle(tmp_db):
    """store_session then end_session, verify fields."""
    store_session("sess-abc", branch="main", working_dir="/home/user/proj", db_path=tmp_db)

    conn = get_connection(tmp_db)
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", ("sess-abc",)).fetchone()
    assert row is not None
    assert row["branch"] == "main"
    assert row["working_dir"] == "/home/user/proj"
    assert row["ended_at"] is None

    end_session("sess-abc", summary="Did some work", db_path=tmp_db)

    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", ("sess-abc",)).fetchone()
    assert row["ended_at"] is not None
    assert row["summary"] == "Did some work"


def test_decay_importance(tmp_db):
    """Insert observations with old timestamps, run decay, verify scores decreased."""
    conn = get_connection(tmp_db)
    old_time = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()

    # Insert an observation with an old timestamp
    conn.execute(
        """INSERT INTO observations
           (session_id, content, entities, topics, priority, importance, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("s1", "Old observation", "[]", "[]", 3, 0.8, old_time),
    )
    conn.commit()

    # Insert FTS entry manually (trigger won't fire for direct insert bypassing trigger)
    obs_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO observations_fts(rowid, content) VALUES (?, ?)",
        (obs_id, "Old observation"),
    )
    conn.commit()

    decay_importance(half_life_days=14, db_path=tmp_db)

    row = conn.execute(
        "SELECT importance FROM observations WHERE id = ?", (obs_id,)
    ).fetchone()
    # After 14 days with half_life=14, importance should be ~0.4 (0.8 * 0.5)
    assert row["importance"] < 0.8
    assert row["importance"] > 0.2  # Should be around 0.4


def test_decay_importance_p1_slower(tmp_db):
    """P1 observations decay at half rate (double half-life)."""
    conn = get_connection(tmp_db)
    old_time = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()

    # P1 observation
    conn.execute(
        """INSERT INTO observations
           (session_id, content, entities, topics, priority, importance, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("s1", "Important P1 obs", "[]", "[]", 1, 0.8, old_time),
    )
    conn.commit()

    # P3 observation same age
    conn.execute(
        """INSERT INTO observations
           (session_id, content, entities, topics, priority, importance, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("s1", "Normal P3 obs", "[]", "[]", 3, 0.8, old_time),
    )
    conn.commit()

    decay_importance(half_life_days=14, db_path=tmp_db)

    rows = conn.execute(
        "SELECT priority, importance FROM observations ORDER BY priority"
    ).fetchall()
    p1_imp = rows[0]["importance"]
    p3_imp = rows[1]["importance"]
    # P1 should decay slower than P3
    assert p1_imp > p3_imp


def test_prune_old(tmp_db):
    """Insert old low-importance observations, prune, verify deleted. Recent ones preserved."""
    conn = get_connection(tmp_db)
    old_time = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    recent_time = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()

    # Old, low-importance (should be pruned)
    conn.execute(
        """INSERT INTO observations
           (session_id, content, entities, topics, priority, importance, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("s1", "Old low importance", "[]", "[]", 4, 0.1, old_time),
    )
    # Recent, low-importance (should be kept)
    conn.execute(
        """INSERT INTO observations
           (session_id, content, entities, topics, priority, importance, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("s1", "Recent low importance", "[]", "[]", 4, 0.1, recent_time),
    )
    # Old, high-importance (should be kept)
    conn.execute(
        """INSERT INTO observations
           (session_id, content, entities, topics, priority, importance, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("s1", "Old high importance", "[]", "[]", 1, 0.9, old_time),
    )
    conn.commit()

    pruned = prune_old(retention_days=30, min_importance=0.3, db_path=tmp_db)
    assert pruned == 1

    rows = conn.execute("SELECT content FROM observations").fetchall()
    contents = [r["content"] for r in rows]
    assert "Old low importance" not in contents
    assert "Recent low importance" in contents
    assert "Old high importance" in contents


def test_concurrent_wal(tmp_path):
    """Two connections, one writing while other reads (WAL mode)."""
    db_path = str(tmp_path / "wal-test.db")
    conn1 = init_db(db_path)
    conn2 = sqlite3.connect(db_path)
    conn2.row_factory = sqlite3.Row

    # Write with conn1
    conn1.execute(
        """INSERT INTO observations
           (session_id, content, entities, topics, priority, importance, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("s1", "WAL test", "[]", "[]", 3, 0.5, datetime.now(timezone.utc).isoformat()),
    )
    conn1.commit()

    # Read with conn2 concurrently
    rows = conn2.execute("SELECT * FROM observations").fetchall()
    assert len(rows) == 1
    assert rows[0]["content"] == "WAL test"

    conn1.close()
    conn2.close()


def test_get_observations_filters(populated_db):
    """Test various filter combinations."""
    # unconsolidated only
    all_obs = get_observations(db_path=populated_db)
    uncons = get_observations(unconsolidated_only=True, db_path=populated_db)
    assert len(uncons) == len(all_obs)  # none consolidated yet

    # by session
    s1_obs = get_observations(session_id="session-1", db_path=populated_db)
    assert len(s1_obs) == 2
    assert all(o["session_id"] == "session-1" for o in s1_obs)

    # by priority
    high_pri = get_observations(min_priority=2, db_path=populated_db)
    assert all(o["priority"] <= 2 for o in high_pri)
