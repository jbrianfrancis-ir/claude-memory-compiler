"""
Seed the knowledge base from existing project files.

Scans the parent project for documentation, planning artifacts, memory files,
and key config files, then builds a synthetic daily log and compiles it into
knowledge articles.

Usage (from project root):
    uv run --directory claude-memory-compiler python scripts/seed.py
    uv run --directory claude-memory-compiler python scripts/seed.py --dry-run
    uv run --directory claude-memory-compiler python scripts/seed.py --sources
    uv run --directory claude-memory-compiler python scripts/seed.py --skip-compile
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

# Paths (this script lives at claude-memory-compiler/scripts/seed.py)
ROOT_DIR = Path(__file__).resolve().parent.parent
DAILY_DIR = ROOT_DIR / "daily"
KNOWLEDGE_DIR = ROOT_DIR / "knowledge"
SCRIPTS_DIR = ROOT_DIR / "scripts"

# The parent project is one level above the claude-memory-compiler dir
PROJECT_DIR = ROOT_DIR.parent

# ── Limits ───────────────────────────────────────────────────────────────
MAX_FILE_CHARS = 8_000
MAX_TOTAL_CHARS = 120_000

# ── Source categories in priority order ──────────────────────────────────
SOURCE_CATEGORIES: list[tuple[str, list[tuple[str, Path]]]] = [
    ("Project Memory", [
        ("MEMORY.md", Path.home() / ".claude" / "projects"),
    ]),
    ("Project Documentation", [
        ("README.md", PROJECT_DIR),
        ("CLAUDE.md", PROJECT_DIR),
        ("docs/*.md", PROJECT_DIR),
        ("docs/**/*.md", PROJECT_DIR),
    ]),
    ("Planning - Codebase Intel", [
        (".planning/codebase/*.md", PROJECT_DIR),
    ]),
    ("Planning - Roadmap & Milestones", [
        (".planning/ROADMAP.md", PROJECT_DIR),
        (".planning/MILESTONES.md", PROJECT_DIR),
        (".planning/STATE.md", PROJECT_DIR),
        (".planning/milestones/*.md", PROJECT_DIR),
    ]),
    ("Planning - Deployment & Ops", [
        (".planning/docs/*.md", PROJECT_DIR),
    ]),
    ("Planning - Phase Summaries", [
        (".planning/phases/**/*SUMMARY*.md", PROJECT_DIR),
    ]),
    ("Planning - Phase Research", [
        (".planning/phases/**/*RESEARCH*.md", PROJECT_DIR),
    ]),
]


def today_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def find_memory_files() -> list[Path]:
    """Find memory files in the Claude projects directory for this project."""
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return []

    # Claude's convention: replace / and . with - in the absolute path
    normalized = re.sub(r"[/.]", "-", str(PROJECT_DIR.resolve()))

    files = []
    for subdir in projects_dir.iterdir():
        if not subdir.is_dir():
            continue
        if subdir.name == normalized:
            memory_dir = subdir / "memory"
            if memory_dir.exists():
                files.extend(sorted(memory_dir.glob("*.md")))
    return files


def discover_sources() -> list[tuple[str, Path]]:
    """Discover all seedable source files, returned as (category, path) pairs."""
    sources: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    for f in find_memory_files():
        resolved = f.resolve()
        if resolved not in seen:
            seen.add(resolved)
            sources.append(("Project Memory", f))

    for category, patterns in SOURCE_CATEGORIES:
        if category == "Project Memory":
            continue
        for pattern, base_dir in patterns:
            for f in sorted(base_dir.glob(pattern)):
                if not f.is_file():
                    continue
                resolved = f.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                sources.append((category, f))

    return sources


def read_source(path: Path) -> str:
    """Read a source file, truncating if too large."""
    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""
    if len(content) > MAX_FILE_CHARS:
        content = content[:MAX_FILE_CHARS] + "\n\n...(truncated)"
    return content


def build_daily_log(sources: list[tuple[str, Path]]) -> str:
    """Build a synthetic daily log from discovered sources."""
    today = datetime.now(timezone.utc).astimezone()
    lines = [
        f"# Daily Log: {today.strftime('%Y-%m-%d')}",
        "",
        "## Sessions",
        "",
        f"### Seed Import ({today.strftime('%H:%M')}) - Knowledge Base Initialization",
        "",
        "**Context:** Seeded knowledge base from existing project files, planning artifacts, "
        "and documentation. This is a retroactive import to bootstrap the knowledge base "
        "with the project's accumulated knowledge.",
        "",
    ]

    grouped: OrderedDict[str, list[Path]] = OrderedDict()
    for category, path in sources:
        grouped.setdefault(category, []).append(path)

    lines.append("**Sources Imported:**")
    for category, paths in grouped.items():
        lines.append(f"- {category}: {len(paths)} file(s)")
    lines.append("")

    total_chars = len("\n".join(lines))

    for category, paths in grouped.items():
        section = ["---", "", f"#### {category}", ""]

        for path in paths:
            content = read_source(path)
            if not content.strip():
                continue

            try:
                rel = path.relative_to(PROJECT_DIR)
            except ValueError:
                try:
                    rel = path.relative_to(Path.home())
                    rel = Path("~") / rel
                except ValueError:
                    rel = path

            entry = f"##### `{rel}`\n\n{content}\n\n"

            if total_chars + len(entry) > MAX_TOTAL_CHARS:
                section.append(f"##### `{rel}`\n\n...(skipped - total size limit reached)\n\n")
                break
            else:
                section.append(entry)
                total_chars += len(entry)

        lines.extend(section)

    lines.extend([
        "---",
        "",
        "**Key Exchanges:**",
        "- Retroactive seed import of project documentation and planning artifacts",
        "- All existing knowledge from CLAUDE.md, memory files, codebase intel, "
        "roadmaps, phase summaries, and deployment docs captured",
        "",
        "**Decisions Made:**",
        "- Bootstrapped knowledge base from existing project state rather than starting empty",
        "",
        "**Lessons Learned:**",
        "- See individual source files above for accumulated project knowledge",
        "",
    ])

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Seed knowledge base from existing project files")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be created")
    parser.add_argument("--sources", action="store_true", help="List discovered sources and exit")
    parser.add_argument("--skip-compile", action="store_true", help="Create daily log only")
    args = parser.parse_args()

    print("Discovering source files...")
    sources = discover_sources()

    if not sources:
        print("No source files found. Nothing to seed.")
        return

    if args.sources:
        print(f"\nFound {len(sources)} source files:\n")
        current_category = None
        for category, path in sources:
            if category != current_category:
                print(f"\n  [{category}]")
                current_category = category
            try:
                rel = path.relative_to(PROJECT_DIR)
            except ValueError:
                rel = path
            size = path.stat().st_size
            print(f"    {rel} ({size:,} bytes)")
        return

    print(f"Found {len(sources)} source files across {len(set(c for c, _ in sources))} categories")

    daily_log = build_daily_log(sources)
    log_path = DAILY_DIR / f"{today_iso()}.md"

    if args.dry_run:
        print(f"\n[DRY RUN] Would write {len(daily_log):,} chars to {log_path.name}")
        print(f"\nPreview (first 2000 chars):\n{'=' * 60}")
        print(daily_log[:2000])
        if len(daily_log) > 2000:
            print(f"\n... ({len(daily_log) - 2000:,} more chars)")
        return

    DAILY_DIR.mkdir(parents=True, exist_ok=True)

    if log_path.exists():
        with open(log_path, "a", encoding="utf-8") as f:
            content = daily_log.split("\n## Sessions\n", 1)
            if len(content) > 1:
                f.write("\n## Sessions\n" + content[1])
            else:
                f.write("\n" + daily_log)
        print(f"Appended seed data to existing {log_path.name}")
    else:
        log_path.write_text(daily_log, encoding="utf-8")
        print(f"Created {log_path.name} ({len(daily_log):,} chars)")

    if args.skip_compile:
        print("\nSkipping compilation (--skip-compile). Run manually:")
        print(f"  uv run --directory claude-memory-compiler python scripts/compile.py --file daily/{log_path.name}")
        return

    print(f"\nCompiling {log_path.name} into knowledge articles...")
    compile_script = SCRIPTS_DIR / "compile.py"
    result = subprocess.run(
        ["uv", "run", "--directory", str(ROOT_DIR), "python", str(compile_script),
         "--file", f"daily/{log_path.name}"],
        cwd=str(ROOT_DIR),
    )

    if result.returncode == 0:
        print("\nSeed complete! Knowledge base is populated.")
    else:
        print(f"\nCompilation exited with code {result.returncode}")
        print("You can retry with:")
        print(f"  uv run --directory claude-memory-compiler python scripts/compile.py --file daily/{log_path.name}")


if __name__ == "__main__":
    main()
