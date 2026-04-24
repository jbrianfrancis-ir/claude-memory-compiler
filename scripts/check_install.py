"""
check_install.py - Verify a claude-memory-compiler install is wired correctly.

Runs a series of checks against the local install and the optional global
knowledge base, printing [OK] / [WARN] / [FAIL] for each, with a remediation
hint on any failure. Exits non-zero if any required check fails.

Usage (from project root):
    uv run --directory claude-memory-compiler python scripts/check_install.py
    uv run --directory claude-memory-compiler python scripts/check_install.py --verbose
    uv run --directory claude-memory-compiler python scripts/check_install.py --no-color
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent              # compiler root
PROJECT_DIR = ROOT.parent                                   # parent project (if integrated)
KNOWLEDGE_DIR = ROOT / "knowledge"
DAILY_DIR = ROOT / "daily"
HOOKS_DIR = ROOT / "hooks"
SCRIPTS_DIR = ROOT / "scripts"
COMPILER_SETTINGS = ROOT / ".claude" / "settings.json"
PROJECT_SETTINGS = PROJECT_DIR / ".claude" / "settings.json"

GLOBAL_KB_DIR = Path.home() / ".claude" / "knowledge-base"
GLOBAL_INDEX = GLOBAL_KB_DIR / "knowledge" / "index.md"
CREDENTIALS = Path.home() / ".claude" / ".credentials.json"

REQUIRED_HOOK_EVENTS = ("SessionStart", "PreCompact", "SessionEnd")


# ── Output helpers ────────────────────────────────────────────────────────

class Color:
    enabled = sys.stdout.isatty()

    @classmethod
    def wrap(cls, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if cls.enabled else text

    @classmethod
    def green(cls, s):  return cls.wrap(s, "32")
    @classmethod
    def yellow(cls, s): return cls.wrap(s, "33")
    @classmethod
    def red(cls, s):    return cls.wrap(s, "31")
    @classmethod
    def dim(cls, s):    return cls.wrap(s, "2")


@dataclass
class Result:
    name: str
    status: str          # "ok" | "warn" | "fail"
    detail: str = ""
    hint: str = ""

    def render(self) -> str:
        tag = {
            "ok":   Color.green("[OK]  "),
            "warn": Color.yellow("[WARN]"),
            "fail": Color.red("[FAIL]"),
        }[self.status]
        line = f"{tag} {self.name}"
        if self.detail:
            line += f" — {Color.dim(self.detail)}"
        return line


# ── Individual checks ─────────────────────────────────────────────────────

def check_uv_installed() -> Result:
    path = shutil.which("uv")
    if path:
        return Result("uv installed", "ok", detail=path)
    return Result(
        "uv installed", "fail",
        hint="Install uv: https://docs.astral.sh/uv/getting-started/installation/",
    )


def check_python_version() -> Result:
    v = sys.version_info
    if (v.major, v.minor) >= (3, 12):
        return Result("Python >= 3.12", "ok", detail=f"{v.major}.{v.minor}.{v.micro}")
    return Result(
        "Python >= 3.12", "fail",
        detail=f"{v.major}.{v.minor}.{v.micro}",
        hint="Install Python 3.12+ (or let uv manage it via `uv python install 3.12`).",
    )


def check_pyproject() -> Result:
    pyproject = ROOT / "pyproject.toml"
    if pyproject.exists():
        return Result("pyproject.toml present", "ok", detail=str(pyproject))
    return Result(
        "pyproject.toml present", "fail",
        hint=f"Missing {pyproject}. Re-clone claude-memory-compiler.",
    )


def check_uv_sync() -> Result:
    lock = ROOT / "uv.lock"
    venv = ROOT / ".venv"
    if not lock.exists():
        return Result("uv.lock present", "fail", hint="Run `uv sync` in the compiler dir.")
    if not venv.exists():
        return Result(
            "uv venv materialised", "warn",
            hint=f"Run `uv sync` in {ROOT} so hooks can spawn quickly.",
        )
    return Result("uv.lock + .venv present", "ok")


def check_agent_sdk_importable() -> Result:
    try:
        proc = subprocess.run(
            ["uv", "run", "--directory", str(ROOT), "python", "-c",
             "import claude_agent_sdk; print(claude_agent_sdk.__name__)"],
            capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return Result("claude-agent-sdk importable", "fail",
                      detail=str(e),
                      hint="Install uv first, then `uv sync` in the compiler dir.")
    if proc.returncode == 0:
        return Result("claude-agent-sdk importable", "ok")
    return Result("claude-agent-sdk importable", "fail",
                  detail=(proc.stderr or proc.stdout).strip().splitlines()[-1:][0]
                         if (proc.stderr or proc.stdout) else "",
                  hint=f"Run `uv sync` in {ROOT}.")


def check_compiler_layout() -> Result:
    # hooks/ and scripts/ ship with the repo and must exist.
    # knowledge/ and daily/ are created lazily on first use.
    required_missing = [p.name for p in (HOOKS_DIR, SCRIPTS_DIR) if not p.exists()]
    optional_missing = [p.name for p in (KNOWLEDGE_DIR, DAILY_DIR) if not p.exists()]
    if required_missing:
        return Result("compiler directory layout", "fail",
                      detail=f"missing: {', '.join(required_missing)}",
                      hint="Re-clone claude-memory-compiler.")
    if optional_missing:
        return Result("compiler directory layout", "warn",
                      detail=f"not yet created: {', '.join(optional_missing)}",
                      hint="These are created on first seed/flush — expected on fresh installs.")
    return Result("compiler directory layout", "ok")


def check_hook_files() -> Result:
    expected = {"session-start.py", "session-end.py", "pre-compact.py"}
    actual = {p.name for p in HOOKS_DIR.glob("*.py")} if HOOKS_DIR.exists() else set()
    missing = expected - actual
    if not missing:
        return Result("hook scripts present", "ok")
    return Result("hook scripts present", "fail",
                  detail=f"missing: {', '.join(sorted(missing))}",
                  hint="Re-clone claude-memory-compiler.")


def _settings_has_hooks(settings_path: Path) -> tuple[bool, list[str]]:
    """Return (all_present, missing_event_names)."""
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, list(REQUIRED_HOOK_EVENTS)
    hooks = data.get("hooks", {})
    missing = [e for e in REQUIRED_HOOK_EVENTS if not hooks.get(e)]
    return not missing, missing


def check_settings_wired() -> Result:
    """Check that the project (preferred) or compiler settings.json wires all
    three hook events. The integrated layout is the documented scenario, so
    we prefer the project-root settings; a compiler-root settings.json only
    counts when the compiler is the project (standalone)."""
    if PROJECT_SETTINGS.exists():
        ok, missing = _settings_has_hooks(PROJECT_SETTINGS)
        if ok:
            return Result("hooks wired (project)", "ok", detail=str(PROJECT_SETTINGS))
        return Result("hooks wired (project)", "fail",
                      detail=f"{PROJECT_SETTINGS} missing events: {', '.join(missing)}",
                      hint=("Replace or merge with "
                            f"{ROOT / 'settings.integrated.example.json'} "
                            "(see INSTALL.md §4)."))
    # Standalone mode: no sibling project settings, but the compiler itself
    # carries a .claude/settings.json that Claude Code would load when run
    # from the compiler dir directly.
    if COMPILER_SETTINGS.exists():
        ok, missing = _settings_has_hooks(COMPILER_SETTINGS)
        if ok:
            return Result("hooks wired (standalone)", "warn",
                          detail=f"using {COMPILER_SETTINGS} — ok for testing the "
                                 "compiler standalone, but the integrated flow "
                                 "expects project-root settings",
                          hint=f"For project use: cp {ROOT / 'settings.integrated.example.json'} {PROJECT_SETTINGS}")
        return Result("hooks wired (standalone)", "fail",
                      detail=f"{COMPILER_SETTINGS} missing events: {', '.join(missing)}")
    return Result("hooks wired", "fail",
                  detail=f"no .claude/settings.json at {PROJECT_SETTINGS}",
                  hint=("cp "
                        f"{ROOT / 'settings.integrated.example.json'} "
                        f"{PROJECT_SETTINGS} (or merge into an existing one)."))


def check_session_start_executes() -> Result:
    script = HOOKS_DIR / "session-start.py"
    if not script.exists():
        return Result("session-start.py runs", "fail", hint="Re-clone.")
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return Result("session-start.py runs", "fail", detail=str(e))
    if proc.returncode != 0:
        return Result("session-start.py runs", "fail",
                      detail=(proc.stderr.strip().splitlines() or ["(no stderr)"])[-1])
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return Result("session-start.py emits valid JSON", "fail",
                      detail="stdout was not JSON",
                      hint="Inspect hooks/session-start.py output manually.")
    ctx = payload.get("hookSpecificOutput", {}).get("additionalContext", "")
    has_global = "Global Knowledge Base Index" in ctx
    has_project = "Project Knowledge Base Index" in ctx
    if has_global and has_project:
        return Result("session-start injects both indexes", "ok")
    if has_project and not has_global:
        return Result("session-start injects both indexes", "warn",
                      detail="only project index present",
                      hint=f"Clone the global KB to {GLOBAL_KB_DIR}.")
    return Result("session-start injects both indexes", "fail",
                  detail="neither index found in output")


def check_global_kb() -> Result:
    if not GLOBAL_KB_DIR.exists():
        return Result("global KB cloned", "warn",
                      detail=f"{GLOBAL_KB_DIR} not found",
                      hint="git clone <your-fork>/claude-knowledge-base.git ~/.claude/knowledge-base")
    if not (GLOBAL_KB_DIR / ".git").exists():
        return Result("global KB is a git repo", "warn",
                      detail=f"{GLOBAL_KB_DIR} exists but has no .git",
                      hint="Re-clone so session-start can auto-pull.")
    if not GLOBAL_INDEX.exists():
        return Result("global KB index.md present", "warn",
                      detail=f"{GLOBAL_INDEX} missing",
                      hint="Your global KB has no articles yet — that is fine for a new install.")
    return Result("global KB ready", "ok", detail=str(GLOBAL_KB_DIR))


def check_credentials() -> Result:
    if CREDENTIALS.exists():
        return Result("Claude Code credentials found", "ok", detail=str(CREDENTIALS))
    return Result("Claude Code credentials found", "warn",
                  detail=f"{CREDENTIALS} not found",
                  hint="Log in to Claude Code once so the Agent SDK can authenticate.")


def check_knowledge_index() -> Result:
    idx = KNOWLEDGE_DIR / "index.md"
    if idx.exists():
        return Result("project knowledge/index.md present", "ok")
    return Result("project knowledge/index.md present", "warn",
                  detail="no index yet — expected on a fresh install",
                  hint="Run scripts/seed.py or have a session flush first.")


# ── Orchestration ─────────────────────────────────────────────────────────

CHECKS = [
    check_uv_installed,
    check_python_version,
    check_pyproject,
    check_uv_sync,
    check_compiler_layout,
    check_hook_files,
    check_settings_wired,
    check_session_start_executes,
    check_global_kb,
    check_knowledge_index,
    check_credentials,
    check_agent_sdk_importable,
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify claude-memory-compiler install.")
    parser.add_argument("--verbose", action="store_true", help="Print hints for all checks, not just failures.")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors.")
    args = parser.parse_args()

    if args.no_color or os.environ.get("NO_COLOR"):
        Color.enabled = False

    print(f"claude-memory-compiler doctor — checking {ROOT}\n")
    results: list[Result] = []
    for fn in CHECKS:
        try:
            r = fn()
        except Exception as e:
            r = Result(fn.__name__, "fail", detail=f"check crashed: {e}")
        results.append(r)
        print(r.render())
        if r.hint and (args.verbose or r.status != "ok"):
            print(f"       {Color.dim('→ ' + r.hint)}")

    fails = sum(1 for r in results if r.status == "fail")
    warns = sum(1 for r in results if r.status == "warn")
    oks = len(results) - fails - warns

    print()
    print(f"{oks} ok, {warns} warning(s), {fails} failure(s)")

    if fails:
        print(Color.red("Install has problems. Fix the [FAIL] items above before using."))
        return 1
    if warns:
        print(Color.yellow("Install is usable. Warnings listed above are optional to fix."))
        return 0
    print(Color.green("All checks passed."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
