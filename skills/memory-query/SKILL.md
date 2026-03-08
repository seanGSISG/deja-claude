---
name: memory-query
description: >-
  This skill should be used when the user asks to search, query, or recall
  project memories, observations, or past session history. Trigger phrases
  include "what do you remember about", "search memory for", "recall",
  "what did we decide about", "find past observations on", "check memory for",
  "show me what we learned about". Also triggered by /memory-query.
arguments:
  - name: query
    description: What to search for in memory (topic, file, concept)
    required: true
---

Search the project memory database for relevant observations and insights.

## Running the Query

Execute the query script using the production venv with system fallback:

```bash
~/.config/claude-memory/.venv/bin/python3 ${CLAUDE_PLUGIN_ROOT}/scripts/query.py "{{query}}" 2>/dev/null || python3 ${CLAUDE_PLUGIN_ROOT}/scripts/query.py "{{query}}"
```

## Understanding Results

Results are ranked by a composite relevance score (0.00–1.00) with these weights:
- **30% recency** — linear decay over 30 days
- **30% priority** — P1=1.0, P2=0.7, P3=0.4, P4=0.2
- **20% importance** — stored importance value (decays over time)
- **20% topic match** — overlap between observation topics and active context

### Result Format

Each result line follows one of these formats:

- `[0.XX] INSIGHT: ...` — A consolidation insight (synthesized from multiple observations, boosted priority)
- `[0.XX] P{N}: ...` — An individual observation with priority level P1–P4
  - P1 = Critical (architectural decisions, security patterns)
  - P2 = Important (bug fixes with root causes, dependency workarounds)
  - P3 = Useful (code patterns, file relationships, test strategies)
  - P4 = Minor (failed approaches, environment details)
- `         Topics: ...` — Topic tags for the preceding observation
- `         Summary: ...` — Summary text for consolidation insights

## Error Handling

- If the script fails or returns empty results, check that the database exists. It should be at a path like `~/.claude/projects/*/claude-memory.db` or at `$CLAUDE_MEMORY_DB_PATH`.
- If the database is missing, suggest running the plugin setup: the user may need to reinstall or re-run setup.
- Never expose raw Python tracebacks to the user — summarize the error instead.

## No Results

If no results are found for the query:
1. Suggest broader or alternative search terms (e.g., if "auth bug" returns nothing, try "authentication" or "login")
2. Check whether the database has any observations at all — an empty database means no sessions have been recorded yet
3. Remind the user that memories are extracted from completed sessions, so very recent work may not yet be indexed

## Follow-up

After presenting results, offer to:
- Search again with different or broader terms
- Run `/memory-status` to check database health and observation counts
- Run `/memory-consolidate` to synthesize unconsolidated observations into insights
