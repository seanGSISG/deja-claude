---
name: memory-setup
description: >-
  This skill should be used when the user asks to set up, configure, or install
  the claude-memory plugin, check memory health, diagnose memory issues, or
  reconfigure their LLM provider. Trigger phrases include "set up memory",
  "configure memory", "memory setup", "setup memory plugin", "diagnose memory",
  "check memory setup", "reconfigure memory provider", "fix memory".
  Also triggered by /memory-setup.
arguments: []
---

Interactive guided setup for the claude-memory plugin. Walk the user through each step, asking for their input before proceeding.

## Step 1: Run Diagnostics

First, check the current state of the installation:

```bash
~/.config/claude-memory/.venv/bin/python3 ${CLAUDE_PLUGIN_ROOT}/scripts/diagnose.py 2>/dev/null || python3 ${CLAUDE_PLUGIN_ROOT}/scripts/diagnose.py
```

This returns a JSON array of check results. Present them as a status table to the user:

| Component | Status | Detail |
|-----------|--------|--------|
| Python version | ok/fail | version number |
| Virtual environment | ok/fail | path |
| ... | ... | ... |

Use checkmarks and X marks for status. If everything is "ok", tell the user the plugin is already configured and ask if they want to reconfigure. If there are failures, explain what needs fixing.

## Step 2: Choose LLM Provider

Ask the user which LLM provider they want to use for observation extraction and consolidation. Present these options clearly:

1. **Google Gemini** (default) — Cheapest option, uses `gemini-3.1-flash-lite-preview`. Requires `GOOGLE_API_KEY`.
2. **Anthropic** — Uses `claude-haiku-4-5-20251001`. Requires `ANTHROPIC_API_KEY`.
3. **OpenAI** — Uses `gpt-4o-mini`. Requires `OPENAI_API_KEY`.
4. **Claude Code pipe mode** — No extra API key needed. Uses the user's existing Claude subscription via `claude -p`. Slightly slower but zero additional cost.
5. **Local endpoint** — Any OpenAI-compatible server (vLLM, Ollama, etc.). Requires a URL.

Wait for the user to choose before proceeding. If the diagnostics showed an API key already set, mention which provider it corresponds to.

## Step 3: API Key (if needed)

If the user chose Google, Anthropic, or OpenAI:

- Check if the corresponding API key environment variable is already set (the diagnostics output shows this)
- If set, confirm with the user that they want to use it
- If not set, tell the user which env var to set:
  - Google: `export GOOGLE_API_KEY="your-key"` (get from https://aistudio.google.com/)
  - Anthropic: `export ANTHROPIC_API_KEY="your-key"`
  - OpenAI: `export OPENAI_API_KEY="your-key"`
- Remind them to add it to their shell profile (`~/.zshrc`, `~/.bashrc`, or `~/.secrets.env`) for persistence
- **IMPORTANT**: Never ask the user to paste their API key directly into the conversation. Always instruct them to set it as an environment variable.

If the user chose "claude" or "local", skip this step.

If the user chose "local", ask for the endpoint URL (default: `http://localhost:8080`).

## Step 4: Model Selection

Ask if they want to use the default model or specify a custom one. Show the default for their chosen provider:

| Provider | Default Model |
|----------|--------------|
| Google | `gemini-3.1-flash-lite-preview` |
| Anthropic | `claude-haiku-4-5-20251001` |
| OpenAI | `gpt-4o-mini` |
| Claude | `haiku` |
| Local | `default` |

Most users should keep the default. Only advanced users need to change this.

## Step 5: Optional Settings

Ask if the user wants to customize these (show defaults — most users should keep them):

- **Retention days** (default: 30) — How many days before low-importance memories are pruned
- **Max inject tokens** (default: 4096) — How much memory context to inject at session start
- **Fallback to Claude** (default: true) — Whether to fall back to `claude -p` if the configured provider's API key is missing

## Step 6: Write Configuration

Once all choices are collected, write the config file:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/write_config.py \
  --provider "<chosen_provider>" \
  --model "<chosen_model>" \
  --fallback-to-claude "<true_or_false>" \
  --retention-days <days> \
  --max-inject-tokens <tokens>
```

For local provider, add `--local-url "<url>"`.

Show the user the resulting config that was written to `~/.config/claude-memory/config.json`.

## Step 7: Run Setup

Execute the plugin setup script to create the venv, install dependencies, and initialize the database:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/setup.sh
```

If this fails, help the user debug based on the error output.

## Step 8: Verify

Run diagnostics again to confirm everything passes:

```bash
~/.config/claude-memory/.venv/bin/python3 ${CLAUDE_PLUGIN_ROOT}/scripts/diagnose.py 2>/dev/null || python3 ${CLAUDE_PLUGIN_ROOT}/scripts/diagnose.py
```

Present the updated status table. All checks should now be "ok".

## Step 9: Summary

Give the user a concise summary of what was set up:

- Config file location: `~/.config/claude-memory/config.json`
- Venv location: `~/.config/claude-memory/.venv/`
- Database location: show the path from diagnostics
- Provider and model configured
- What happens next: memories will be automatically recorded, extracted, and injected starting from the next session

Remind the user about available slash commands:
- `/memory-query <topic>` — Search memories
- `/memory-status` — Check database health
- `/memory-consolidate` — Manually trigger consolidation
- `/memory-forget <query>` — Remove observations

## Conversation Style

- Be friendly and conversational — this is an onboarding experience
- Wait for the user's answer at each decision point before proceeding
- Use clear numbered options when asking for choices
- If the user seems unsure, recommend the default
- If diagnostics show everything is already set up, offer to show current config or reconfigure
