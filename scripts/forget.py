"""Memory forget/deletion tool for claude-memory plugin.

Supports soft forget (reduce importance for next prune cycle) and
hard forget (immediate deletion). Always requires --confirm for mutations.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from storage import get_connection, get_db_path, init_db, search_observations


def find_consolidation_links(observation_ids: list[int], db_path: str) -> list[int]:
    """Find consolidation IDs that reference any of the given observation IDs."""
    conn = get_connection(db_path)
    rows = conn.execute("SELECT id, source_ids FROM consolidations").fetchall()
    linked = []
    obs_set = set(observation_ids)
    for row in rows:
        source_ids = json.loads(row["source_ids"]) if isinstance(row["source_ids"], str) else row["source_ids"]
        if obs_set & set(source_ids):
            linked.append(row["id"])
    return linked


def preview(query: str, db_path: str | None = None) -> dict:
    """Search for observations matching the query and return preview."""
    if db_path is None:
        db_path = get_db_path()

    try:
        init_db(db_path)
    except Exception as e:
        return {"error": f"Failed to open database: {e}"}

    matches = search_observations(query, limit=50, db_path=db_path)
    match_ids = [m["id"] for m in matches]
    consolidation_links = find_consolidation_links(match_ids, db_path) if match_ids else []

    return {
        "match_count": len(matches),
        "matches": [
            {
                "id": m["id"],
                "content": m["content"],
                "priority": m["priority"],
                "importance": m.get("importance", 0.5),
                "topics": m.get("topics", []),
                "created_at": m.get("created_at", ""),
            }
            for m in matches
        ],
        "consolidation_links": consolidation_links,
    }


def forget(query: str, mode: str = "soft", db_path: str | None = None) -> dict:
    """Execute forget operation on matching observations."""
    if db_path is None:
        db_path = get_db_path()

    try:
        init_db(db_path)
    except Exception as e:
        return {"error": f"Failed to open database: {e}"}

    matches = search_observations(query, limit=50, db_path=db_path)
    if not matches:
        return {"affected": 0, "mode": mode, "message": "No matching observations found."}

    conn = get_connection(db_path)
    match_ids = [m["id"] for m in matches]
    placeholders = ",".join("?" * len(match_ids))

    if mode == "hard":
        conn.execute(f"DELETE FROM observations WHERE id IN ({placeholders})", match_ids)
    else:
        conn.execute(
            f"UPDATE observations SET importance = 0.05 WHERE id IN ({placeholders})",
            match_ids,
        )

    conn.commit()

    return {
        "affected": len(match_ids),
        "mode": mode,
        "affected_ids": match_ids,
        "message": f"{'Deleted' if mode == 'hard' else 'Soft-forgotten'} {len(match_ids)} observations.",
    }


def main():
    parser = argparse.ArgumentParser(description="Forget/delete memory observations")
    parser.add_argument("--query", required=True, help="Search term to find observations")
    parser.add_argument("--mode", choices=["soft", "hard"], default="soft",
                        help="soft = reduce importance, hard = delete immediately")
    parser.add_argument("--preview", action="store_true", help="Preview matches without modifying")
    parser.add_argument("--confirm", action="store_true", help="Required to execute mutations")
    args = parser.parse_args()

    if args.preview:
        result = preview(args.query)
        print(json.dumps(result, indent=2))
        return

    if not args.confirm:
        print(json.dumps({"error": "Safety gate: --confirm flag required to execute forget operation."}))
        sys.exit(1)

    result = forget(args.query, mode=args.mode)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
