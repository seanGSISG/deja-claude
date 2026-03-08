"""Background consolidation pipeline for claude-memory plugin.

Finds cross-session patterns, resolves contradictions, and manages
memory lifecycle (decay + pruning).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from llm_provider import get_provider
from storage import (
    decay_importance,
    get_connection,
    get_consolidations,
    get_db_path,
    get_observations,
    init_db,
    prune_old,
    store_consolidation,
)

MIN_OBSERVATIONS_THRESHOLD = 3
MAX_OBSERVATIONS_BATCH = 20
LOCK_STALE_SECONDS = 600  # 10 minutes

CONSOLIDATION_PROMPT = """You are a Memory Consolidation Agent. You find patterns, connections, and insights
across coding session observations -- like a brain consolidating during sleep.

Here are unconsolidated observations from recent sessions:

{observations_json}

And recent consolidation history for context:

{consolidations_json}

Your tasks:
1. Find connections between observations (cross-session patterns)
2. Create a synthesized summary across related observations
3. Identify one key insight or pattern
4. Detect contradictions with previous consolidations and resolve them
5. Note which observations are redundant or superseded

For connections, provide pairs of observation IDs with relationship descriptions.

Respond with ONLY JSON (no markdown fencing, no explanation):
{{
  "summary": "Synthesized summary across observations...",
  "insight": "One key pattern or insight discovered...",
  "connections": [
    {{"from_id": 1, "to_id": 5, "relationship": "both relate to auth module refactoring"}}
  ],
  "source_ids": [1, 3, 5, 7],
  "contradictions": [
    {{"observation_id": 2, "contradicts": "Previous said X, but observation shows Y", "resolution": "Y is correct because..."}}
  ],
  "redundant_ids": [4]
}}"""


def get_memory_dir() -> str:
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        project_dir = str(Path.home() / ".claude" / "projects" / "default")
    return os.path.join(project_dir, "claude-memory")


def get_lock_path() -> Path:
    return Path(get_memory_dir()) / "consolidate.lock"


def acquire_lock(lock_path: Path) -> bool:
    """Try to acquire a lock file. Returns True if acquired."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path.exists():
        try:
            lock_data = json.loads(lock_path.read_text())
            lock_pid = lock_data.get("pid", 0)
            lock_time = lock_data.get("timestamp", 0)

            if time.time() - lock_time > LOCK_STALE_SECONDS:
                try:
                    os.kill(lock_pid, 0)
                except (OSError, ProcessLookupError):
                    lock_path.unlink(missing_ok=True)
                else:
                    return False
            else:
                return False
        except (json.JSONDecodeError, ValueError):
            lock_path.unlink(missing_ok=True)

    lock_path.write_text(json.dumps({"pid": os.getpid(), "timestamp": time.time()}))
    return True


def release_lock(lock_path: Path) -> None:
    lock_path.unlink(missing_ok=True)


def parse_consolidation_response(response: str) -> dict | None:
    """Parse JSON response from consolidation LLM."""
    text = response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            try:
                result = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        else:
            return None

    if not isinstance(result, dict):
        return None

    return result


def run_consolidation(db_path: str | None = None) -> dict | None:
    """Run a single consolidation cycle. Returns result dict on success, None on failure.

    Callers that check truthiness are backward-compatible (dict is truthy, None is falsy).
    """
    if db_path is None:
        db_path = get_db_path()

    try:
        init_db(db_path)
    except Exception as e:
        print(f"Failed to init DB: {e}", file=sys.stderr)
        return None

    # Get unconsolidated observations
    observations = get_observations(
        limit=MAX_OBSERVATIONS_BATCH,
        unconsolidated_only=True,
        db_path=db_path,
    )

    if len(observations) < MIN_OBSERVATIONS_THRESHOLD:
        print(f"Only {len(observations)} unconsolidated observations (need >= {MIN_OBSERVATIONS_THRESHOLD}), skipping.", file=sys.stderr)
        return None

    # Get recent consolidation history for context
    history = get_consolidations(limit=5, db_path=db_path)

    # Build prompt
    obs_json = json.dumps(
        [{"id": o["id"], "content": o["content"], "entities": o["entities"],
          "topics": o["topics"], "priority": o["priority"], "session_id": o["session_id"]}
         for o in observations],
        indent=2,
    )
    hist_json = json.dumps(
        [{"id": c["id"], "summary": c["summary"], "insight": c["insight"]}
         for c in history],
        indent=2,
    ) if history else "[]"

    prompt = CONSOLIDATION_PROMPT.format(
        observations_json=obs_json,
        consolidations_json=hist_json,
    )

    # Call LLM
    try:
        provider = get_provider()
        response = provider.complete(prompt, "Consolidate these observations.")
    except Exception as e:
        print(f"LLM call failed: {e}", file=sys.stderr)
        return None

    # Parse response
    result = parse_consolidation_response(response)
    if not result:
        print("Failed to parse consolidation response.", file=sys.stderr)
        return None

    # Extract fields
    summary = result.get("summary", "")
    insight = result.get("insight", "")
    connections = result.get("connections", [])
    source_ids = result.get("source_ids", [])
    redundant_ids = result.get("redundant_ids", [])

    if not summary and not insight:
        print("Empty consolidation result, skipping.", file=sys.stderr)
        return None

    # Validate source_ids against actual observation IDs
    valid_ids = {o["id"] for o in observations}
    source_ids = [sid for sid in source_ids if sid in valid_ids]
    if not source_ids:
        source_ids = [o["id"] for o in observations]

    # Validate connections
    valid_connections = []
    for conn in connections:
        if isinstance(conn, dict) and conn.get("from_id") in valid_ids and conn.get("to_id") in valid_ids:
            valid_connections.append(conn)

    # Store consolidation
    store_consolidation(
        source_ids=source_ids,
        summary=summary,
        insight=insight,
        connections=valid_connections,
        db_path=db_path,
    )

    # Handle redundant observations: set importance to 0.1
    if redundant_ids:
        conn = get_connection(db_path)
        for rid in redundant_ids:
            if rid in valid_ids:
                conn.execute(
                    "UPDATE observations SET importance = 0.1 WHERE id = ?",
                    (rid,),
                )
        conn.commit()

    # Run decay and pruning
    retention_days = int(os.environ.get("CLAUDE_MEMORY_RETENTION_DAYS", "30"))
    decay_importance(half_life_days=14, db_path=db_path)
    prune_old(retention_days=retention_days, min_importance=0.3, db_path=db_path)

    consolidation_result = {
        "summary": summary,
        "insight": insight,
        "connections": valid_connections,
        "source_ids": source_ids,
        "redundant_ids": [rid for rid in redundant_ids if rid in valid_ids],
        "observation_count": len(observations),
    }

    print(f"Consolidation complete: {len(source_ids)} observations consolidated.", file=sys.stderr)
    return consolidation_result


def run_with_lock(db_path: str | None = None) -> bool:
    """Run consolidation with lock file protection."""
    lock_path = get_lock_path()

    if not acquire_lock(lock_path):
        print("Another consolidation is running, skipping.", file=sys.stderr)
        return False

    try:
        return run_consolidation(db_path)
    except Exception as e:
        print(f"Consolidation failed: {e}", file=sys.stderr)
        return False
    finally:
        release_lock(lock_path)


def dry_run(db_path: str | None = None) -> None:
    """Preview what would be consolidated without executing."""
    if db_path is None:
        db_path = get_db_path()

    try:
        init_db(db_path)
    except Exception as e:
        print(json.dumps({"error": f"Failed to init DB: {e}"}))
        return

    observations = get_observations(
        limit=MAX_OBSERVATIONS_BATCH,
        unconsolidated_only=True,
        db_path=db_path,
    )

    result = {
        "unconsolidated_count": len(observations),
        "threshold": MIN_OBSERVATIONS_THRESHOLD,
        "would_consolidate": len(observations) >= MIN_OBSERVATIONS_THRESHOLD,
        "observations": [
            {"id": o["id"], "content": o["content"][:200], "priority": o["priority"],
             "topics": o["topics"], "importance": o.get("importance", 0.5)}
            for o in observations
        ],
    }
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Consolidate memory observations")
    parser.add_argument("--continuous", type=int, default=0,
                        help="Run every N minutes (0 = one-shot)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would be consolidated without executing")
    parser.add_argument("--foreground", action="store_true",
                        help="Run consolidation directly (no lock) and print result to stdout")
    args = parser.parse_args()

    if args.dry_run:
        dry_run()
        return

    if args.foreground:
        result = run_consolidation()
        if result:
            print(json.dumps(result, indent=2))
        else:
            print(json.dumps({"status": "no_consolidation", "message": "Nothing to consolidate or consolidation failed."}))
        return

    if args.continuous > 0:
        interval = args.continuous * 60
        while True:
            run_with_lock()
            time.sleep(interval)
    else:
        run_with_lock()


if __name__ == "__main__":
    main()
