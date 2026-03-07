"""Memory query engine for claude-memory plugin.

Gathers context signals, scores observations by relevance, and returns
ranked results for injection or CLI display.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from storage import (
    get_consolidations,
    get_db_path,
    get_observations,
    init_db,
    search_observations,
)


def gather_context_signals() -> dict:
    """Collect git branch, working directory, recent files, and topic keywords."""
    signals: dict = {
        "branch": "",
        "working_dir": os.getcwd(),
        "recent_files": [],
        "active_topics": set(),
    }

    # Git branch
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            signals["branch"] = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Recent files from git
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~5"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
            signals["recent_files"] = files[:10]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Extract topic keywords from branch and recent files
    topics = set()
    if signals["branch"]:
        # Parse branch name for keywords (e.g., "feature/auth-refactor" -> ["auth", "refactor"])
        for part in signals["branch"].replace("/", "-").replace("_", "-").split("-"):
            if len(part) > 2:
                topics.add(part.lower())

    for f in signals["recent_files"]:
        # Extract directory and file stem as topics
        p = Path(f)
        if p.stem and len(p.stem) > 2:
            topics.add(p.stem.lower())
        if p.parent.name and len(p.parent.name) > 2:
            topics.add(p.parent.name.lower())

    signals["active_topics"] = topics
    return signals


def relevance_score(observation: dict, context: dict) -> float:
    """Score an observation's relevance to current context.

    Weights: recency 30%, priority 30%, importance 20%, topic match 20%.
    """
    score = 0.0
    now = datetime.now(timezone.utc)

    # Recency: linear decay over 30 days
    created_str = observation.get("created_at", "")
    if created_str:
        try:
            created = datetime.fromisoformat(created_str)
            age_days = (now - created).total_seconds() / 86400
            recency = max(0.0, 1.0 - (age_days / 30))
            score += recency * 0.3
        except (ValueError, TypeError):
            pass

    # Priority: P1=1.0, P2=0.7, P3=0.4, P4=0.2
    priority = observation.get("priority", 3)
    priority_weight = {1: 1.0, 2: 0.7, 3: 0.4, 4: 0.2}
    score += priority_weight.get(priority, 0.2) * 0.3

    # Importance
    importance = observation.get("importance", 0.5)
    score += importance * 0.2

    # Topic match
    obs_topics = observation.get("topics", [])
    if isinstance(obs_topics, str):
        obs_topics = json.loads(obs_topics) if obs_topics else []
    active_topics = context.get("active_topics", set())
    if obs_topics and active_topics:
        overlap = len(set(t.lower() for t in obs_topics) & set(t.lower() for t in active_topics))
        score += min(overlap / 3, 1.0) * 0.2

    return round(score, 4)


def query_memories(
    db_path: str | None = None,
    context_signals: dict | None = None,
    max_results: int = 30,
) -> list[dict]:
    """Query and rank memories by relevance.

    Returns a list of dicts with 'score' and 'type' ('consolidation' or 'observation') added.
    """
    if db_path is None:
        db_path = get_db_path()

    try:
        init_db(db_path)
    except Exception:
        return []

    if context_signals is None:
        context_signals = gather_context_signals()

    candidates: list[dict] = []
    seen_ids: set[tuple[str, int]] = set()

    def add_candidate(item: dict, item_type: str) -> None:
        key = (item_type, item.get("id", 0))
        if key not in seen_ids:
            seen_ids.add(key)
            item["_type"] = item_type
            item["_score"] = relevance_score(item, context_signals)
            candidates.append(item)

    # 1. Recent consolidation insights (last 10)
    for c in get_consolidations(limit=10, db_path=db_path):
        # Consolidations get a boost: use priority=1 equivalent for scoring
        c["priority"] = 1
        c["importance"] = 0.8
        c["topics"] = []
        add_candidate(c, "consolidation")

    # 2. High-priority observations (P1-P2, last 30 days, limit 30)
    for obs in get_observations(limit=30, min_priority=2, db_path=db_path):
        add_candidate(obs, "observation")

    # 3. Topic-matched observations via FTS
    active_topics = context_signals.get("active_topics", set())
    if active_topics:
        search_query = " OR ".join(active_topics)
        try:
            for obs in search_observations(search_query, limit=20, db_path=db_path):
                add_candidate(obs, "observation")
        except Exception:
            pass  # FTS query syntax errors

    # Score and rank
    candidates.sort(key=lambda c: c["_score"], reverse=True)
    return candidates[:max_results]


def format_cli_results(results: list[dict]) -> str:
    """Format query results for CLI display."""
    if not results:
        return "No memories found."

    lines = []
    for r in results:
        score = r.get("_score", 0)
        rtype = r.get("_type", "observation")
        if rtype == "consolidation":
            lines.append(f"[{score:.2f}] INSIGHT: {r.get('insight', '')}")
            lines.append(f"         Summary: {r.get('summary', '')}")
        else:
            priority = r.get("priority", 3)
            lines.append(f"[{score:.2f}] P{priority}: {r.get('content', '')}")
            topics = r.get("topics", [])
            if topics:
                lines.append(f"         Topics: {', '.join(topics)}")
        lines.append("")

    return "\n".join(lines)


def main():
    """CLI entry point: python query.py "search term" """
    if len(sys.argv) < 2:
        print("Usage: python query.py <search_term>", file=sys.stderr)
        sys.exit(1)

    search_term = " ".join(sys.argv[1:])
    db_path = get_db_path()

    try:
        init_db(db_path)
    except Exception as e:
        print(f"Error initializing DB: {e}", file=sys.stderr)
        sys.exit(1)

    # Build context signals with search term as additional topic
    context = gather_context_signals()
    context["active_topics"].add(search_term.lower())

    results = query_memories(db_path=db_path, context_signals=context)

    # Also add direct FTS results
    try:
        fts_results = search_observations(search_term, limit=10, db_path=db_path)
        seen = {r.get("id") for r in results if r.get("_type") == "observation"}
        for obs in fts_results:
            if obs.get("id") not in seen:
                obs["_type"] = "observation"
                obs["_score"] = relevance_score(obs, context)
                results.append(obs)
    except Exception:
        pass

    results.sort(key=lambda r: r.get("_score", 0), reverse=True)
    print(format_cli_results(results[:20]))


if __name__ == "__main__":
    main()
