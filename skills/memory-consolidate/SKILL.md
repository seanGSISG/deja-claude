---
name: memory-consolidate
description: >-
  This skill should be used when the user asks to consolidate memories,
  merge observations, synthesize insights, run consolidation, or clean up memory.
  Trigger phrases include "consolidate memories", "merge observations",
  "run consolidation", "synthesize insights", "clean up memory".
arguments:
  - name: dry_run
    description: If "true", preview what would be consolidated without executing
    required: false
---

Manually trigger memory consolidation to synthesize observations into insights.

## Prerequisite Check

Before consolidating, check how many unconsolidated observations exist. Consolidation requires at least 3 unconsolidated observations.

## Dry Run (Preview)

If the user wants to preview, or if `{{dry_run}}` is "true", run a dry run first:

```bash
~/.config/claude-memory/.venv/bin/python3 ${CLAUDE_PLUGIN_ROOT}/scripts/consolidate.py --dry-run 2>/dev/null || python3 ${CLAUDE_PLUGIN_ROOT}/scripts/consolidate.py --dry-run
```

This outputs JSON showing:
- `unconsolidated_count` — how many observations are pending
- `threshold` — minimum needed (3)
- `would_consolidate` — whether consolidation would proceed
- `observations` — list of observations that would be consolidated (id, content preview, priority, topics, importance)

Present this as a readable preview to the user. If `would_consolidate` is false, explain that more observations are needed.

## Running Consolidation

If the user confirms (or didn't request dry run), execute consolidation in the foreground:

```bash
~/.config/claude-memory/.venv/bin/python3 ${CLAUDE_PLUGIN_ROOT}/scripts/consolidate.py --foreground 2>/dev/null || python3 ${CLAUDE_PLUGIN_ROOT}/scripts/consolidate.py --foreground
```

This outputs JSON with the consolidation result:
- `summary` — Synthesized summary across related observations
- `insight` — Key pattern or insight discovered
- `connections` — Links found between observations
- `source_ids` — IDs of observations that were consolidated
- `redundant_ids` — Observations marked as redundant (importance reduced)
- `observation_count` — Total observations that were processed

If the result is `{"status": "no_consolidation", ...}`, explain that consolidation couldn't proceed (not enough observations, or LLM call failed).

## Presenting Results

Show the user:
1. The synthesized **summary** across observations
2. The key **insight** discovered
3. How many observations were consolidated and how many marked redundant
4. Any **connections** found between observations (cross-session patterns)

## Error Handling

- If consolidation is already running (lock contention), inform the user and suggest trying again shortly
- If the LLM call fails, check API key configuration (`GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, etc.)
- If no observations exist, suggest the user complete some coding sessions first
- Consolidation also runs decay and pruning — observations with very low importance may be removed
