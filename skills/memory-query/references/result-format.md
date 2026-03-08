# Memory Query Result Format Reference

## Scoring Algorithm

Results are ranked by a composite relevance score calculated in `query.py:79-116`.

### Weights

| Component | Weight | Calculation |
|-----------|--------|-------------|
| Recency | 30% | Linear decay: `max(0, 1 - age_days/30)` — 0 after 30 days |
| Priority | 30% | P1=1.0, P2=0.7, P3=0.4, P4=0.2 |
| Importance | 20% | Raw importance value (0.0–1.0, decays with half-life of 14 days) |
| Topic Match | 20% | `min(overlap_count/3, 1.0)` — overlap between observation and active context topics |

### Score Range

- Maximum possible: 1.0 (new P1 observation with importance 1.0 and full topic match)
- Typical high-relevance: 0.60–0.85
- Typical moderate: 0.30–0.60
- Low relevance: below 0.30

## Priority Levels

| Level | Name | Examples |
|-------|------|----------|
| P1 | Critical | Architectural decisions with rationale, security-sensitive patterns |
| P2 | Important | Bug fixes with root causes, dependency quirks and workarounds |
| P3 | Useful | File relationships, code patterns/conventions, test strategies |
| P4 | Minor | Failed approaches, dead ends, environment setup details |

P1 observations decay at half the rate of others (half-life of 28 days vs 14 days).

## Consolidation Insights

Consolidation insights are scored with `priority=1` and `importance=0.8` for boosted ranking. They represent synthesized patterns found across multiple sessions.

## Injection Context (session-start)

The injection system (`inject.py`) organizes results into sections by priority:

1. **Key Insights** — Consolidation insights
2. **Known Issues** — P1/P2 observations containing bug/error/fix keywords
3. **Recent Decisions** — P1/P2 observations (architectural decisions)
4. **Patterns** — P3 observations
5. **Context** — P4 observations

Sections are filled in order within a configurable token budget (default 4096 tokens, configurable via `CLAUDE_MEMORY_MAX_INJECT_TOKENS`).
