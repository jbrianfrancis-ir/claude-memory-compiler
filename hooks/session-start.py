"""
SessionStart hook - injects knowledge base context into every conversation.

Pulls the latest global knowledge base (if present) and reads both the global
and project indexes plus the most recent project daily log, then emits them as
`additionalContext` so Claude always "remembers" what it has learned across
machines and across projects.

Configure in .claude/settings.json:
{
    "hooks": {
        "SessionStart": [{
            "matcher": "",
            "hooks": [{
                "type": "command",
                "command": "uv run --directory claude-memory-compiler python hooks/session-start.py",
                "timeout": 15
            }]
        }]
    }
}
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Project (per-repo) paths — relative to the compiler root.
ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = ROOT / "knowledge"
DAILY_DIR = ROOT / "daily"
PROJECT_INDEX_FILE = KNOWLEDGE_DIR / "index.md"

# Global (cross-machine) knowledge base — synced via git.
GLOBAL_KB_DIR = Path.home() / ".claude" / "knowledge-base"
GLOBAL_INDEX_FILE = GLOBAL_KB_DIR / "knowledge" / "index.md"

MAX_CONTEXT_CHARS = 20_000
MAX_LOG_LINES = 30
GIT_PULL_TIMEOUT_SECS = 5


def pull_global_kb() -> None:
    """Best-effort fetch of the global KB. Silent on any failure so the hook
    never blocks a session (offline, no remote, detached HEAD, etc.)."""
    if not (GLOBAL_KB_DIR / ".git").exists():
        return
    try:
        subprocess.run(
            ["git", "-C", str(GLOBAL_KB_DIR), "pull", "--quiet", "--ff-only"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=GIT_PULL_TIMEOUT_SECS,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass


def read_index(path: Path, label: str) -> str:
    if path.exists():
        return f"## {label}\n\n{path.read_text(encoding='utf-8')}"
    return f"## {label}\n\n(empty or not installed)"


def get_recent_log() -> str:
    """Read the most recent project daily log (today or yesterday)."""
    today = datetime.now(timezone.utc).astimezone()
    for offset in range(2):
        date = today - timedelta(days=offset)
        log_path = DAILY_DIR / f"{date.strftime('%Y-%m-%d')}.md"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").splitlines()
            recent = lines[-MAX_LOG_LINES:] if len(lines) > MAX_LOG_LINES else lines
            return "\n".join(recent)
    return "(no recent daily log)"


def build_context() -> str:
    parts = []

    today = datetime.now(timezone.utc).astimezone()
    parts.append(f"## Today\n{today.strftime('%A, %B %d, %Y')}")

    parts.append(read_index(GLOBAL_INDEX_FILE, "Global Knowledge Base Index"))
    parts.append(read_index(PROJECT_INDEX_FILE, "Project Knowledge Base Index"))

    parts.append(f"## Recent Project Daily Log\n\n{get_recent_log()}")

    context = "\n\n---\n\n".join(parts)
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n\n...(truncated)"
    return context


def main():
    pull_global_kb()
    context = build_context()
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
