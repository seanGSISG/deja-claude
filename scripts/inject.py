"""Format scored memories as markdown for context injection.

Builds a concise markdown document within a token budget, organized
by section priority: insights > known issues > decisions > patterns > context.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from query import gather_context_signals, query_memories
from storage import get_db_path, init_db

DEFAULT_MAX_TOKENS = 4096


def estimate_tokens(text: str) -> int:
    """Rough token estimate: chars / 4."""
    return len(text) // 4


def format_injection(scored_candidates: list[dict], max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    """Build markdown injection within token budget.

    Section priority order:
    1. Key Insights (consolidation insights)
    2. Known Issues (P1/P2 bug/problem observations)
    3. Recent Decisions (P1/P2 architectural decisions)
    4. Patterns (P3 observations)
    5. Context (P4 observations)
    """
    if not scored_candidates:
        return ""

    sections: dict[str, list[str]] = {
        "Key Insights": [],
        "Known Issues": [],
        "Recent Decisions": [],
        "Patterns": [],
        "Context": [],
    }

    used_ids: set[tuple[str, int]] = set()
    session_ids: set[str] = set()

    for candidate in scored_candidates:
        ctype = candidate.get("_type", "observation")
        cid = (ctype, candidate.get("id", 0))

        if cid in used_ids:
            continue
        used_ids.add(cid)

        if ctype == "consolidation":
            insight = candidate.get("insight", "")
            if insight:
                sections["Key Insights"].append(f"- {insight}")
        else:
            content = candidate.get("content", "")
            if not content:
                continue

            priority = candidate.get("priority", 3)
            session_id = candidate.get("session_id", "")
            if session_id:
                session_ids.add(session_id)

            bullet = f"- {content}"

            if priority <= 2:
                # Classify as issue or decision based on content keywords
                lower = content.lower()
                if any(kw in lower for kw in ("bug", "error", "fail", "flaky", "broken", "issue", "fix")):
                    sections["Known Issues"].append(bullet)
                else:
                    sections["Recent Decisions"].append(bullet)
            elif priority == 3:
                sections["Patterns"].append(bullet)
            else:
                sections["Context"].append(bullet)

    # Build markdown within token budget
    parts = ["# Project Memory (auto-injected)\n"]
    budget_chars = max_tokens * 4
    current_chars = len(parts[0])

    footer = f"\n---\n*{len(used_ids)} memories from {len(session_ids)} sessions | Query: /memory-query <topic>*\n"
    budget_chars -= len(footer)

    for section_name in ["Key Insights", "Known Issues", "Recent Decisions", "Patterns", "Context"]:
        items = sections[section_name]
        if not items:
            continue

        header = f"\n## {section_name}\n"
        section_text = header + "\n".join(items) + "\n"

        if current_chars + len(section_text) > budget_chars:
            # Try to fit partial section
            remaining = budget_chars - current_chars - len(header)
            if remaining > 50:
                parts.append(header)
                current_chars += len(header)
                for item in items:
                    if current_chars + len(item) + 1 > budget_chars:
                        break
                    parts.append(item + "\n")
                    current_chars += len(item) + 1
            break
        else:
            parts.append(section_text)
            current_chars += len(section_text)

    parts.append(footer)
    return "".join(parts)


def build_injection_context(
    session_id: str | None = None,
    db_path: str | None = None,
) -> str:
    """Full pipeline: gather signals, query, format.

    Returns formatted markdown string, or empty string on any failure.
    """
    try:
        if db_path is None:
            db_path = get_db_path()

        if not Path(db_path).exists():
            return ""

        init_db(db_path)
        context = gather_context_signals()
        results = query_memories(db_path=db_path, context_signals=context)

        if not results:
            return ""

        max_tokens = int(os.environ.get("CLAUDE_MEMORY_MAX_INJECT_TOKENS", DEFAULT_MAX_TOKENS))
        return format_injection(results, max_tokens=max_tokens)

    except Exception:
        return ""


def main():
    """CLI entry point for injection (used by session-start.sh)."""
    import argparse

    parser = argparse.ArgumentParser(description="Build memory injection context")
    parser.add_argument("--session-id", default=None, help="Session ID")
    parser.add_argument("--db-path", default=None, help="Database path override")
    args = parser.parse_args()

    result = build_injection_context(
        session_id=args.session_id,
        db_path=args.db_path,
    )
    if result:
        print(result, end="")


if __name__ == "__main__":
    main()
