"""Session transcript -> observations extraction pipeline.

Runs as a background process triggered by the SessionEnd hook.
Uses an LLM to distill structured observations from session recordings.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Add scripts dir to path
sys.path.insert(0, str(Path(__file__).parent))

from storage import get_db_path, store_observation, end_session, get_observations
from llm_provider import get_provider

MAX_OBSERVATIONS = 20
MAX_TOKENS_ESTIMATE = 8000  # ~8K tokens, estimated as chars/4
LOCK_STALE_SECONDS = 600  # 10 minutes

EXTRACTION_PROMPT = """You are a Session Observer. You analyze coding session transcripts and extract
structured observations -- facts, decisions, patterns, and insights worth remembering.

For this session transcript, extract observations in the following categories:

**P1 (Critical)** -- Architectural decisions with rationale, security-sensitive patterns
**P2 (Important)** -- Bug fixes with root causes and solutions, dependency quirks and workarounds
**P3 (Useful)** -- File relationships and cross-file dependencies, code patterns/conventions established, test strategies that worked
**P4 (Minor)** -- Failed approaches and dead ends, environment setup details

For each observation, provide:
- content: A precise, factual statement (include file paths, error messages, function names -- never approximate)
- entities: Key entities mentioned (files, functions, packages, services)
- topics: 2-4 topic tags
- priority: P1, P2, P3, or P4
- importance: Float 0.0 to 1.0

Rules:
- Extract concrete facts, not vague summaries
- Include exact file paths, function names, error messages
- Capture the "why" behind decisions, not just the "what"
- Note what DIDN'T work (failed approaches are valuable)
- Maximum 20 observations per session
- Skip trivial file reads and searches that led nowhere

Respond with ONLY a JSON array of observations (no markdown fencing, no explanation):
[
  {
    "content": "...",
    "entities": ["src/auth.py", "JWT", "refresh_token()"],
    "topics": ["authentication", "security"],
    "priority": "P2",
    "importance": 0.7
  }
]"""

PRIORITY_MAP = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}


def get_memory_dir() -> str:
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        project_dir = str(Path.home() / ".claude" / "projects" / "default")
    return os.path.join(project_dir, "claude-memory")


def get_lock_path() -> Path:
    return Path(get_memory_dir()) / "extract.lock"


def acquire_lock(lock_path: Path) -> bool:
    """Try to acquire a lock file. Returns True if acquired."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path.exists():
        try:
            lock_data = json.loads(lock_path.read_text())
            lock_pid = lock_data.get("pid", 0)
            lock_time = lock_data.get("timestamp", 0)

            # Check if stale: older than 10 minutes and PID is dead
            if time.time() - lock_time > LOCK_STALE_SECONDS:
                try:
                    os.kill(lock_pid, 0)  # Check if process exists
                except (OSError, ProcessLookupError):
                    # PID is dead, lock is stale
                    lock_path.unlink(missing_ok=True)
                else:
                    return False  # Process still alive
            else:
                return False  # Lock is fresh
        except (json.JSONDecodeError, ValueError):
            lock_path.unlink(missing_ok=True)

    lock_path.write_text(json.dumps({"pid": os.getpid(), "timestamp": time.time()}))
    return True


def release_lock(lock_path: Path) -> None:
    lock_path.unlink(missing_ok=True)


def parse_session_log(log_path: str) -> str:
    """Read session JSONL event log and return summarized text."""
    if not Path(log_path).exists():
        return ""

    lines = []
    for line in Path(log_path).read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            event_type = event.get("event_type", "")
            if event_type == "tool_use":
                lines.append(f"[{event.get('tool_name', '')}] {event.get('tool_input_summary', '')}")
            elif event_type == "pre_compact":
                lines.append(f"[compact] {event.get('context_summary', '')}")
            elif event_type == "session_end":
                lines.append("[session ended]")
            else:
                lines.append(f"[{event_type}]")
        except json.JSONDecodeError:
            continue
    return "\n".join(lines)


def parse_transcript(transcript_path: str) -> str:
    """Read Claude Code transcript JSONL, filter and summarize."""
    if not transcript_path or not Path(transcript_path).exists():
        return ""

    lines = []
    for line in Path(transcript_path).read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        entry_type = entry.get("type", "")

        # Skip thinking entries
        if entry_type == "thinking":
            continue

        if entry_type == "user":
            content = entry.get("content", "")
            if isinstance(content, str):
                lines.append(f"User: {content[:500]}")
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        lines.append(f"User: {item.get('text', '')[:500]}")

        elif entry_type == "assistant":
            content = entry.get("content", "")
            if isinstance(content, str):
                lines.append(f"Assistant: {content[:500]}")
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            lines.append(f"Assistant: {item.get('text', '')[:500]}")
                        elif item.get("type") == "tool_use":
                            lines.append(f"Tool call: {item.get('name', '')}({json.dumps(item.get('input', {}))[:200]})")

        elif entry_type == "tool_use":
            name = entry.get("name", "")
            inp = json.dumps(entry.get("input", {}))[:200]
            lines.append(f"Tool call: {name}({inp})")

        elif entry_type == "tool_result":
            content = entry.get("content", "")
            if isinstance(content, str):
                lines.append(f"Tool result: {content[:200]}")

    return "\n".join(lines)


def truncate_to_tokens(text: str, max_tokens: int = MAX_TOKENS_ESTIMATE) -> str:
    """Truncate text to approximately max_tokens (estimated as chars/4).
    Prioritize recent entries by keeping the end of the text."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return "...[truncated]\n" + text[-max_chars:]


def parse_llm_response(response: str) -> list[dict]:
    """Parse JSON array of observations from LLM response."""
    # Strip markdown code fences if present
    text = response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (code fences)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        observations = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON array in the response
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            try:
                observations = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return []
        else:
            return []

    if not isinstance(observations, list):
        return []

    return observations[:MAX_OBSERVATIONS]


def run_extraction(session_id: str, transcript_path: str) -> None:
    """Main extraction pipeline."""
    lock_path = get_lock_path()

    if not acquire_lock(lock_path):
        print("Another extraction is running, skipping.", file=sys.stderr)
        return

    try:
        # Read session event log
        memory_dir = get_memory_dir()
        session_log = os.path.join(memory_dir, "sessions", f"{session_id}.jsonl")
        session_text = parse_session_log(session_log)

        # Read transcript
        transcript_text = parse_transcript(transcript_path)

        # Combine and truncate
        combined = f"=== Session Activity ===\n{session_text}\n\n=== Session Transcript ===\n{transcript_text}"
        combined = truncate_to_tokens(combined)

        if len(combined.strip()) < 50:
            print("Session too short for extraction, skipping.", file=sys.stderr)
            return

        # Get LLM provider and extract
        provider = get_provider()
        response = provider.complete(EXTRACTION_PROMPT, combined)

        # Parse observations
        observations = parse_llm_response(response)
        if not observations:
            print("No observations extracted.", file=sys.stderr)
            return

        # Store observations
        db_path = get_db_path()
        stored_count = 0
        for obs in observations:
            content = obs.get("content", "")
            if not content:
                continue

            priority_str = obs.get("priority", "P3")
            priority = PRIORITY_MAP.get(priority_str, 3)
            importance = float(obs.get("importance", 0.5))
            entities = obs.get("entities", [])
            topics = obs.get("topics", [])
            source_file = obs.get("source_file")

            store_observation(
                session_id=session_id,
                content=content,
                entities=entities if isinstance(entities, list) else [],
                topics=topics if isinstance(topics, list) else [],
                priority=priority,
                importance=importance,
                source_file=source_file,
                db_path=db_path,
            )
            stored_count += 1

        # Update session
        summary = f"Extracted {stored_count} observations"
        end_session(session_id, summary=summary, db_path=db_path)

        # If >= 3 unconsolidated observations, spawn consolidation
        unconsolidated = get_observations(unconsolidated_only=True, db_path=db_path)
        if len(unconsolidated) >= 3:
            scripts_dir = Path(__file__).parent
            consolidate_script = scripts_dir / "consolidate.py"
            if consolidate_script.exists():
                os.system(
                    f'nohup python3 "{consolidate_script}" > /dev/null 2>&1 &'
                )

        print(f"Extraction complete: {stored_count} observations stored.", file=sys.stderr)

    except Exception as e:
        print(f"Extraction failed: {e}", file=sys.stderr)
    finally:
        release_lock(lock_path)


def main():
    parser = argparse.ArgumentParser(description="Extract observations from a session")
    parser.add_argument("--session-id", required=True, help="Session ID")
    parser.add_argument("--transcript", required=True, help="Path to transcript JSONL")
    args = parser.parse_args()

    run_extraction(args.session_id, args.transcript)


if __name__ == "__main__":
    main()
