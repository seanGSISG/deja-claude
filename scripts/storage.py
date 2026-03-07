"""SQLite storage layer for claude-memory plugin.

Provides CRUD operations for observations, consolidations, and sessions
with FTS5 full-text search and WAL mode for concurrent access.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_CONTENT_LENGTH = 2000

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    content TEXT NOT NULL,
    entities TEXT NOT NULL DEFAULT '[]',
    topics TEXT NOT NULL DEFAULT '[]',
    priority INTEGER NOT NULL DEFAULT 3,
    importance REAL NOT NULL DEFAULT 0.5,
    source_file TEXT,
    created_at TEXT NOT NULL,
    consolidated INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS consolidations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_ids TEXT NOT NULL,
    summary TEXT NOT NULL,
    insight TEXT NOT NULL,
    connections TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,
    branch TEXT,
    working_dir TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    summary TEXT
);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
    content,
    content='observations',
    content_rowid='id'
);
"""

FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS observations_ai AFTER INSERT ON observations BEGIN
    INSERT INTO observations_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS observations_ad AFTER DELETE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS observations_au AFTER UPDATE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO observations_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


def get_db_path() -> str:
    """Derive database path from environment or fallback."""
    if path := os.environ.get("CLAUDE_MEMORY_DB_PATH"):
        return path
    if project_dir := os.environ.get("CLAUDE_PROJECT_DIR"):
        return os.path.join(project_dir, "claude-memory.db")
    # Fallback: hash of cwd under ~/.claude/projects/
    cwd_hash = hashlib.md5(os.getcwd().encode()).hexdigest()[:12]
    base = Path.home() / ".claude" / "projects" / cwd_hash
    base.mkdir(parents=True, exist_ok=True)
    return str(base / "claude-memory.db")


def init_db(db_path: str) -> sqlite3.Connection:
    """Create tables, enable WAL mode, return connection. Idempotent."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # FTS virtual table and triggers must be created separately
    conn.executescript(FTS_SCHEMA)
    conn.executescript(FTS_TRIGGERS)
    return conn


# Module-level connection cache
_connections: dict[str, sqlite3.Connection] = {}


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Get or create a connection for the given db path."""
    if db_path is None:
        db_path = get_db_path()
    if db_path not in _connections or _connections[db_path] is None:
        _connections[db_path] = init_db(db_path)
    return _connections[db_path]


def close_connection(db_path: str | None = None) -> None:
    """Close and remove a cached connection."""
    if db_path is None:
        db_path = get_db_path()
    if conn := _connections.pop(db_path, None):
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def store_observation(
    session_id: str,
    content: str,
    entities: list[str],
    topics: list[str],
    priority: int,
    importance: float,
    source_file: str | None = None,
    db_path: str | None = None,
) -> int:
    """Insert an observation and return its ID."""
    content = content[:MAX_CONTENT_LENGTH]
    priority = max(1, min(4, priority))
    importance = max(0.0, min(1.0, importance))

    conn = get_connection(db_path)
    cursor = conn.execute(
        """INSERT INTO observations
           (session_id, content, entities, topics, priority, importance, source_file, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            content,
            json.dumps(entities),
            json.dumps(topics),
            priority,
            importance,
            source_file,
            _now_iso(),
        ),
    )
    conn.commit()
    return cursor.lastrowid


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a Row to dict with parsed JSON fields."""
    d = dict(row)
    for field in ("entities", "topics", "connections", "source_ids"):
        if field in d and isinstance(d[field], str):
            d[field] = json.loads(d[field])
    return d


def get_observations(
    limit: int = 50,
    unconsolidated_only: bool = False,
    session_id: str | None = None,
    min_priority: int | None = None,
    db_path: str | None = None,
) -> list[dict]:
    """Query observations with filters."""
    conn = get_connection(db_path)
    clauses = []
    params: list = []

    if unconsolidated_only:
        clauses.append("consolidated = 0")
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    if min_priority is not None:
        clauses.append("priority <= ?")
        params.append(min_priority)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    query = f"SELECT * FROM observations{where} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def search_observations(
    query: str, limit: int = 20, db_path: str | None = None
) -> list[dict]:
    """FTS5 search on observation content, ranked by relevance."""
    conn = get_connection(db_path)
    rows = conn.execute(
        """SELECT o.*, rank
           FROM observations_fts fts
           JOIN observations o ON o.id = fts.rowid
           WHERE observations_fts MATCH ?
           ORDER BY rank
           LIMIT ?""",
        (query, limit),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def store_consolidation(
    source_ids: list[int],
    summary: str,
    insight: str,
    connections: list[dict],
    db_path: str | None = None,
) -> int:
    """Store a consolidation, mark sources, update bidirectional connections."""
    conn = get_connection(db_path)

    conn.execute(
        "INSERT INTO consolidations (source_ids, summary, insight, connections, created_at) VALUES (?, ?, ?, ?, ?)",
        (json.dumps(source_ids), summary, insight, json.dumps(connections), _now_iso()),
    )
    consolidation_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Update bidirectional connections (pattern from agent.py:213-222)
    for conn_entry in connections:
        from_id = conn_entry.get("from_id")
        to_id = conn_entry.get("to_id")
        relationship = conn_entry.get("relationship", "")
        if from_id and to_id:
            for obs_id in [from_id, to_id]:
                row = conn.execute(
                    "SELECT entities FROM observations WHERE id = ?", (obs_id,)
                ).fetchone()
                if row:
                    existing = json.loads(row["entities"])
                    linked = to_id if obs_id == from_id else from_id
                    existing.append(
                        {"linked_to": linked, "relationship": relationship}
                    )
                    conn.execute(
                        "UPDATE observations SET entities = ? WHERE id = ?",
                        (json.dumps(existing), obs_id),
                    )

    # Mark source observations as consolidated
    if source_ids:
        placeholders = ",".join("?" * len(source_ids))
        conn.execute(
            f"UPDATE observations SET consolidated = 1 WHERE id IN ({placeholders})",
            source_ids,
        )

    conn.commit()
    return consolidation_id


def get_consolidations(
    limit: int = 10, db_path: str | None = None
) -> list[dict]:
    """Return recent consolidation records."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM consolidations ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def store_session(
    session_id: str,
    branch: str | None = None,
    working_dir: str | None = None,
    db_path: str | None = None,
) -> None:
    """Register a new session."""
    conn = get_connection(db_path)
    conn.execute(
        """INSERT OR IGNORE INTO sessions (session_id, branch, working_dir, started_at)
           VALUES (?, ?, ?, ?)""",
        (session_id, branch, working_dir, _now_iso()),
    )
    conn.commit()


def end_session(
    session_id: str,
    summary: str | None = None,
    db_path: str | None = None,
) -> None:
    """Finalize a session with end time and optional summary."""
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE sessions SET ended_at = ?, summary = ? WHERE session_id = ?",
        (_now_iso(), summary, session_id),
    )
    conn.commit()


def decay_importance(
    half_life_days: int = 14, db_path: str | None = None
) -> None:
    """Apply exponential decay to importance scores.

    P1 observations use half_life * 2 for slower decay.
    """
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT id, priority, importance, created_at FROM observations WHERE importance > 0.01"
    ).fetchall()

    now = datetime.now(timezone.utc)
    for row in rows:
        created = datetime.fromisoformat(row["created_at"])
        age_days = (now - created).total_seconds() / 86400
        hl = half_life_days * 2 if row["priority"] == 1 else half_life_days
        new_importance = row["importance"] * math.pow(0.5, age_days / hl)
        conn.execute(
            "UPDATE observations SET importance = ? WHERE id = ?",
            (round(new_importance, 4), row["id"]),
        )

    conn.commit()


def prune_old(
    retention_days: int = 30,
    min_importance: float = 0.3,
    db_path: str | None = None,
) -> int:
    """Remove expired low-importance observations. Returns count pruned."""
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc)
    cutoff = now.isoformat()

    cursor = conn.execute(
        """DELETE FROM observations
           WHERE importance < ?
           AND julianday(?) - julianday(created_at) > ?""",
        (min_importance, cutoff, retention_days),
    )
    conn.commit()
    return cursor.rowcount


if __name__ == "__main__":
    if "--init" in sys.argv:
        path = get_db_path()
        conn = init_db(path)
        conn.close()
        print(f"Database initialized at {path}")
