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

## Configuration

Config file: `~/.config/claude-memory/config.json` (auto-created on first run)

```json
{
  "provider": "google",
  "model": "gemini-3.1-flash-lite-preview",
  "fallback_to_claude": true,
  "claude_model": "haiku"
}
```

### LLM Providers

| Provider | Config value | API key env var | Notes |
|----------|-------------|-----------------|-------|
| Google Gemini | `"google"` | `GOOGLE_API_KEY` | Default. Fast, free tier available |
| Claude Code | `"claude"` | None needed | Uses `claude -p` with your subscription |
| Anthropic API | `"anthropic"` | `ANTHROPIC_API_KEY` | Direct API access |
| OpenAI | `"openai"` | `OPENAI_API_KEY` | GPT-4o-mini default |
| Local | `"local"` | None | OpenAI-compatible endpoint |

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

## Slash Command

```
/memory-query authentication
```

Search your project's memory database for relevant observations and insights.

## How It Works

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

### Hooks

| Event | Script | Purpose |
|-------|--------|---------|
| SessionStart | `session-start.sh` | Register session, inject memories |
| PostToolUse | `post-tool-use.sh` | Record tool events to JSONL log |
| PreCompact | `pre-compact.sh` | Snapshot before context compaction |
| Stop | `stop.sh` | Capture stop event |
| SessionEnd | `session-end.sh` | Trigger background extraction |

## License

MIT
