---
name: memory-forget
description: >-
  This skill should be used when the user wants to forget something, delete memories,
  remove observations, clear outdated information, or reduce memory importance.
  Trigger phrases include "forget about", "delete memories", "remove observations",
  "clear memory of", "forget what we learned about".
arguments:
  - name: query
    description: Search term to find observations to forget
    required: true
  - name: mode
    description: '"soft" reduces importance to 0.05 (pruned next cycle), "hard" deletes immediately. Default soft.'
    required: false
---

Find and remove or diminish specific observations from memory.

## Step 1: Preview Matches (Always)

**Always preview first** — never delete without showing what will be affected.

```bash
~/.config/claude-memory/.venv/bin/python3 ${CLAUDE_PLUGIN_ROOT}/scripts/forget.py --query "{{query}}" --preview 2>/dev/null || python3 ${CLAUDE_PLUGIN_ROOT}/scripts/forget.py --query "{{query}}" --preview
```

This outputs JSON with matching observations:
- `matches` — Array of observations matching the query (id, content, priority, importance, topics, created_at)
- `match_count` — Number of matches found
- `consolidation_links` — Consolidation IDs that reference any matched observations (these observations contributed to synthesized insights)

Present the matches clearly to the user, highlighting:
- The content of each matching observation
- Priority level (P1 observations are critical — extra caution)
- Whether any matches are linked to consolidations (deleting them doesn't undo the insight, but removes the source)

## Step 2: Safety Checks

Before proceeding, verify:

1. **Wildcard queries**: If `{{query}}` is `*`, `everything`, `all`, or similar broad terms, warn the user that this will affect ALL observations and require explicit double confirmation.
2. **P1 observations**: If any matches are P1 (critical), highlight this and warn that these contain architectural decisions or security patterns.
3. **Consolidation-linked**: If matches are linked to consolidations, note that the synthesized insights will remain but their source observations will be affected.

## Step 3: Execute with Confirmation

Only after the user explicitly confirms, execute the forget operation:

**Soft mode** (default — reduces importance so observations are pruned in the next consolidation cycle):
```bash
~/.config/claude-memory/.venv/bin/python3 ${CLAUDE_PLUGIN_ROOT}/scripts/forget.py --query "{{query}}" --mode soft --confirm 2>/dev/null || python3 ${CLAUDE_PLUGIN_ROOT}/scripts/forget.py --query "{{query}}" --mode soft --confirm
```

**Hard mode** (immediate deletion — irreversible):
```bash
~/.config/claude-memory/.venv/bin/python3 ${CLAUDE_PLUGIN_ROOT}/scripts/forget.py --query "{{query}}" --mode hard --confirm 2>/dev/null || python3 ${CLAUDE_PLUGIN_ROOT}/scripts/forget.py --query "{{query}}" --mode hard --confirm
```

## Understanding Modes

- **Soft** (default): Sets importance to 0.05. These observations will be naturally pruned during the next consolidation cycle (when importance drops below threshold). This is reversible if caught before the next prune.
- **Hard**: Immediately deletes observations from the database. FTS triggers automatically clean up the search index. This is irreversible.

## Error Handling

- If no matches are found, suggest alternative search terms
- If the database is locked, suggest trying again shortly
- Never execute without the `--confirm` flag — the script will refuse to modify data without it
