---
name: memory-query
description: Query your project memory for observations, insights, and session history
arguments:
  - name: query
    description: What to search for in memory (topic, file, concept)
    required: true
---

Search the project memory database for relevant observations and insights.

1. Run the query script:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/query.py "{{query}}"
   ```

2. Present the results organized by relevance, showing:
   - Matching observations with their priority and age
   - Related consolidation insights
   - Connected observations

3. If no results found, suggest broader search terms.
