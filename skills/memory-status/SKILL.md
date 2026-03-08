---
name: memory-status
description: >-
  This skill should be used when the user asks about memory database statistics,
  memory health, how many memories are stored, database size, or observation counts.
  Trigger phrases include "memory stats", "how many memories", "memory status",
  "show memory health", "database size", "observation count".
arguments: []
---

Display memory database statistics and health information.

## Running the Status Check

```bash
~/.config/claude-memory/.venv/bin/python3 ${CLAUDE_PLUGIN_ROOT}/scripts/status.py 2>/dev/null || python3 ${CLAUDE_PLUGIN_ROOT}/scripts/status.py
```

## Understanding the Output

The script outputs JSON with these fields:

- **db_path** — Location of the database file
- **db_size_bytes** / **db_size_human** — Database file size
- **observation_count** — Total observations stored
- **unconsolidated_count** — Observations not yet processed by consolidation
- **consolidated_count** — Observations already consolidated
- **consolidation_count** — Number of consolidation cycles completed
- **session_count** — Total sessions recorded
- **priority_distribution** — Breakdown by P1/P2/P3/P4 priority levels
- **importance_stats** — Min, max, and average importance scores
- **oldest_observation** / **newest_observation** — Timestamp range of stored observations

## Presenting Results

Format the JSON output as a readable summary for the user. Highlight:
- Total memories and how many are unconsolidated
- Priority distribution (are most memories P3/P4, or are there critical P1s?)
- Database size and age range
- If many observations are unconsolidated (>10), suggest running `/memory-consolidate`

## Error Handling

- If the database doesn't exist, inform the user that no sessions have been recorded yet
- If the database is locked, it may be mid-extraction — suggest trying again shortly
- Never expose raw tracebacks — summarize errors clearly
