"""Memory database status and statistics.

Provides a snapshot of database health: observation counts, priority distribution,
consolidation stats, session counts, and file size.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from storage import get_connection, get_db_path, init_db


def human_size(size_bytes: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def get_status(db_path: str | None = None) -> dict:
    """Gather database statistics."""
    if db_path is None:
        db_path = get_db_path()

    db_file = Path(db_path)
    if not db_file.exists():
        return {"error": "Database not found", "db_path": db_path}

    try:
        init_db(db_path)
    except Exception as e:
        return {"error": f"Failed to open database: {e}", "db_path": db_path}

    conn = get_connection(db_path)

    # Observation counts
    total = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    unconsolidated = conn.execute("SELECT COUNT(*) FROM observations WHERE consolidated = 0").fetchone()[0]
    consolidated = total - unconsolidated

    # Priority distribution
    priority_rows = conn.execute(
        "SELECT priority, COUNT(*) as cnt FROM observations GROUP BY priority ORDER BY priority"
    ).fetchall()
    priority_dist = {f"P{row[0]}": row[1] for row in priority_rows}

    # Importance stats
    importance_row = conn.execute(
        "SELECT MIN(importance), MAX(importance), AVG(importance) FROM observations"
    ).fetchone()
    importance_stats = {
        "min": round(importance_row[0], 4) if importance_row[0] is not None else None,
        "max": round(importance_row[1], 4) if importance_row[1] is not None else None,
        "avg": round(importance_row[2], 4) if importance_row[2] is not None else None,
    }

    # Timestamps
    oldest = conn.execute("SELECT MIN(created_at) FROM observations").fetchone()[0]
    newest = conn.execute("SELECT MAX(created_at) FROM observations").fetchone()[0]

    # Consolidation and session counts
    consolidation_count = conn.execute("SELECT COUNT(*) FROM consolidations").fetchone()[0]
    session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    # File size
    db_size = db_file.stat().st_size

    return {
        "db_path": db_path,
        "db_size_bytes": db_size,
        "db_size_human": human_size(db_size),
        "observation_count": total,
        "unconsolidated_count": unconsolidated,
        "consolidated_count": consolidated,
        "consolidation_count": consolidation_count,
        "session_count": session_count,
        "priority_distribution": priority_dist,
        "importance_stats": importance_stats,
        "oldest_observation": oldest,
        "newest_observation": newest,
    }


def main():
    result = get_status()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
