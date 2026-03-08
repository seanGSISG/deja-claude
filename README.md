# deja-claude

**Because Claude should remember what happened last time.**

Always-on memory for Claude Code. Automatically records sessions, extracts observations, consolidates insights, and injects relevant context into new sessions.

## Quick Install

```
/plugin marketplace add seanGSISG/deja-claude
/plugin install claude-memory@deja-claude
```

Or for development:

```bash
claude --plugin-dir ~/projects/deja-claude
```

## First-Time Setup

Run the interactive setup wizard after installing:

```
/memory-setup
```

This walks you through choosing an LLM provider, setting your API key, and configuring options. Everything is saved to `~/.config/claude-memory/config.json`.

## What It Does

```
Session 1: You fix an auth bug using JWT with RS256 signing
                    |
                    v
            [SessionEnd hook]
                    |
                    v
        [Extract observations via LLM]
            "Auth uses JWT RS256"
            "Config.py holds JWT settings"
                    |
                    v
        [Consolidate across sessions]
            "JWT config centralized in config.py"
                    |
                    v
Session 2: Claude already knows about JWT setup
            (auto-injected at session start)
```

### The Memory Cycle

1. **Record** -- Hooks capture tool calls and session activity into JSONL logs
2. **Extract** -- After each session, an LLM distills raw activity into structured observations (P1-P4 priority)
3. **Consolidate** -- Between sessions, a background process finds cross-session patterns and resolves contradictions
4. **Inject** -- At session start, relevant memories are auto-injected into Claude's context

## Slash Commands

| Command | Description |
|---------|-------------|
| `/memory-setup` | Interactive guided setup -- provider, API key, config |
| `/memory-query <topic>` | Search memories for a topic, file, or concept |
| `/memory-status` | Database stats -- observation counts, priority distribution, DB size |
| `/memory-consolidate` | Manually trigger consolidation (with optional dry-run preview) |
| `/memory-forget <query>` | Find and remove observations (soft or hard delete, with confirmation) |

## Configuration

Config file: `~/.config/claude-memory/config.json` (created by `/memory-setup` or auto-created on first run)

```json
{
  "provider": "google",
  "model": "gemini-3.1-flash-lite-preview",
  "fallback_to_claude": true,
  "retention_days": 30,
  "max_inject_tokens": 4096
}
```

### LLM Providers

| Provider | Config value | API key env var | Notes |
|----------|-------------|-----------------|-------|
| Google Gemini | `"google"` | `GOOGLE_API_KEY` | Default. Fast, free tier available |
| Claude Code | `"claude"` | None needed | Uses `claude -p` with your subscription |
| Anthropic API | `"anthropic"` | `ANTHROPIC_API_KEY` | Direct API access |
| OpenAI | `"openai"` | `OPENAI_API_KEY` | GPT-4o-mini default |
| Local | `"local"` | None | Any OpenAI-compatible endpoint (vLLM, Ollama) |

### Auto-Fallback

If your configured provider's API key is missing, deja-claude automatically falls back to `claude -p` (pipe mode) which uses your existing Claude Code subscription. No extra API key needed.

Disable with `"fallback_to_claude": false` in config.

### Environment Variable Overrides

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_MEMORY_PROVIDER` | `google` | Override provider |
| `CLAUDE_MEMORY_MODEL` | per-provider | Override model |
| `CLAUDE_MEMORY_API_KEY` | -- | Universal API key override |
| `CLAUDE_MEMORY_MAX_INJECT_TOKENS` | `4096` | Max tokens injected at session start |
| `CLAUDE_MEMORY_RETENTION_DAYS` | `30` | Auto-prune memories older than this |
| `CLAUDE_MEMORY_DB_PATH` | auto-derived | Override database file location |

## How It Works

### Hooks

| Event | Script | Purpose |
|-------|--------|---------|
| SessionStart | `session-start.sh` | Register session, inject memories |
| PreToolUse | `pre-tool-use.sh` | Advisory warnings for dangerous operations + memory search |
| PostToolUse | `post-tool-use.sh` | Record tool events to JSONL log |
| PreCompact | `pre-compact.sh` | Snapshot before context compaction |
| Stop | `stop.sh` | Capture stop event |
| SessionEnd | `session-end.sh` | Trigger background extraction |

### Storage

- SQLite database per project at `$CLAUDE_PROJECT_DIR/claude-memory.db`
- WAL mode for concurrent access
- FTS5 full-text search on observations
- Session JSONL logs at `$CLAUDE_PROJECT_DIR/claude-memory/sessions/`

### Observation Priority

| Priority | Description | Decay half-life |
|----------|-------------|-----------------|
| P1 | Architectural decisions, security patterns | 28 days |
| P2 | Bug fixes with root causes, dependency quirks | 14 days |
| P3 | File relationships, code patterns, test strategies | 14 days |
| P4 | Failed approaches, environment setup | 14 days |

### Relevance Scoring

Memories are ranked by a composite score when injected or queried:

| Component | Weight | Calculation |
|-----------|--------|-------------|
| Recency | 30% | Linear decay over 30 days |
| Priority | 30% | P1=1.0, P2=0.7, P3=0.4, P4=0.2 |
| Importance | 20% | Stored value (decays with half-life) |
| Topic Match | 20% | Overlap with current git context |

### Memory Lifecycle

1. **Extraction** -- After each session, observations stored with initial importance
2. **Decay** -- Importance decays exponentially (14-day half-life; P1 uses 28-day)
3. **Consolidation** -- When >= 3 unconsolidated observations exist, LLM synthesizes patterns
4. **Pruning** -- Observations older than 30 days with importance below 0.3 are removed

## CLI Scripts

```bash
# Search memories
python3 scripts/query.py "authentication"

# Database health check
python3 scripts/status.py

# Run consolidation manually
python3 scripts/consolidate.py

# Preview what would be consolidated
python3 scripts/consolidate.py --dry-run

# Run consolidation with result output
python3 scripts/consolidate.py --foreground

# Preview observations to forget
python3 scripts/forget.py --query "outdated" --preview

# Soft-forget (pruned next cycle)
python3 scripts/forget.py --query "outdated" --mode soft --confirm

# Hard-forget (immediate delete)
python3 scripts/forget.py --query "outdated" --mode hard --confirm

# Run diagnostics
python3 scripts/diagnose.py

# Initialize database
python3 scripts/storage.py --init
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| No memories injected | Complete at least one session first -- extraction runs after SessionEnd |
| Extraction not running | Check your LLM API key, or set `CLAUDE_MEMORY_PROVIDER=claude` |
| Lock file blocking | Auto-expires after 10 minutes. Check with `scripts/diagnose.py` |
| Want to reconfigure | Run `/memory-setup` again |

## License

MIT
