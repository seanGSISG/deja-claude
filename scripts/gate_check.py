"""PreToolUse memory gating — advisory warnings for dangerous operations.

Checks Bash commands for danger patterns and searches memory for relevant
warnings when files are being modified. Never blocks — outputs warnings
as context on stdout, empty output means silent pass-through.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from storage import get_db_path, init_db, search_observations

DANGER_PATTERNS = [
    (r"\bgit\s+push\b", "git push"),
    (r"\brm\s+-rf\b", "rm -rf"),
    (r"\brm\s+-r\b", "rm -r (recursive delete)"),
    (r"\bdocker\s+rm\b", "docker rm"),
    (r"\bDROP\s+TABLE\b", "DROP TABLE"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard"),
    (r"\bgit\s+clean\b", "git clean"),
    (r"\bchmod\s+777\b", "chmod 777"),
]


def check_bash_danger(command: str) -> list[str]:
    """Check a bash command for danger patterns. Returns list of matched descriptions."""
    matched = []
    for pattern, description in DANGER_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            matched.append(description)
    return matched


def search_memory_for_file(file_path: str, db_path: str) -> list[dict]:
    """Search memory for warnings related to a file path."""
    if not file_path:
        return []

    # Build search terms from file path
    p = Path(file_path)
    search_terms = []
    if p.name:
        search_terms.append(p.name)
    if p.stem and p.stem != p.name:
        search_terms.append(p.stem)
    if p.parent.name and p.parent.name not in (".", "/"):
        search_terms.append(p.parent.name)

    if not search_terms:
        return []

    try:
        init_db(db_path)
    except Exception:
        return []

    query = " OR ".join(search_terms)
    try:
        results = search_observations(query, limit=10, db_path=db_path)
    except Exception:
        return []

    # Filter to P1/P2 with importance > 0.3
    return [
        r for r in results
        if r.get("priority", 4) <= 2 and r.get("importance", 0) > 0.3
    ]


def main():
    """Read tool use event from stdin, check for warnings, output to stdout."""
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    warnings = []
    db_path = get_db_path()

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        dangers = check_bash_danger(command)
        if dangers:
            warnings.append(f"Detected potentially dangerous operation: {', '.join(dangers)}")
            # Search memory for related warnings
            for term in dangers:
                key_word = term.split()[0]  # e.g., "git" from "git push"
                try:
                    init_db(db_path)
                    results = search_observations(key_word, limit=5, db_path=db_path)
                    for r in results:
                        if r.get("priority", 4) <= 2 and r.get("importance", 0) > 0.3:
                            warnings.append(f"- [P{r['priority']}] {r['content']}")
                except Exception:
                    pass

    elif tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        related = search_memory_for_file(file_path, db_path)
        if related:
            warnings.append(f"Memory has relevant observations for {file_path}:")
            for r in related:
                warnings.append(f"- [P{r['priority']}] {r['content']}")

    if warnings:
        print("Memory Warning — relevant observations for this operation:")
        for w in warnings:
            print(w)


if __name__ == "__main__":
    main()
