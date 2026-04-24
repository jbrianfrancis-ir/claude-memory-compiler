"""
Promote technology/framework articles from the local KB to the global KB.

Scans local knowledge articles, identifies those with technology/framework
scope (via tags or explicit frontmatter), copies them to the global KB at
~/.claude/knowledge-base/, updates the global index, commits, and pushes.

Usage:
    uv run python promote.py                    # promote eligible articles
    uv run python promote.py --dry-run          # show what would be promoted
    uv run python promote.py --list             # list articles with scope classification
    uv run python promote.py --force <slug>     # force-promote a specific article
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
KNOWLEDGE_DIR = ROOT_DIR / "knowledge"

GLOBAL_KB_DIR = Path.home() / ".claude" / "knowledge-base"
GLOBAL_KNOWLEDGE_DIR = GLOBAL_KB_DIR / "knowledge"
GLOBAL_INDEX_FILE = GLOBAL_KNOWLEDGE_DIR / "index.md"
GLOBAL_LOG_FILE = GLOBAL_KNOWLEDGE_DIR / "log.md"

# Tags that indicate an article is about a technology/framework (global scope).
# If ANY tag matches, the article is eligible for promotion.
GLOBAL_TAGS = {
    # Frameworks & runtimes
    "aspire", "dotnet", "nextjs", "react", "blazor", "express", "django",
    "fastendpoints", "graphql", "hot-chocolate",
    # Cloud & infrastructure
    "azure", "aws", "gcp", "docker", "kubernetes", "containers", "terraform",
    "deployment", "azd", "devops", "ci-cd",
    # Auth & security
    "keycloak", "oidc", "jwt", "oauth", "auth0", "authentication", "authorization",
    # Data & ORMs
    "ef-core", "entity-framework", "sql-server", "postgres", "redis", "sqlbulkcopy",
    "epplus", "dapper",
    # AI & ML
    "anthropic", "claude", "openai", "ai", "llm", "embeddings", "rag",
    # Frontend
    "tailwind", "ant-design", "css", "typescript", "javascript",
    # Observability
    "opentelemetry", "grafana", "prometheus", "logging", "tracing",
    # General tech
    "api", "rest", "streaming", "sse", "websocket", "grpc",
    "infrastructure", "orchestration", "architecture",
}

# Tags that indicate an article is project-specific (never promote).
# If ANY tag matches, the article stays local regardless of other tags.
PROJECT_TAGS = {
    "project", "overview", "billing", "lender", "credit-reporting",
    "lender-analytics", "funnel", "workflow",
}


def parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter as a simple dict."""
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end == -1:
        return {}
    fm = {}
    for line in content[3:end].splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            # Parse list values like [tag1, tag2]
            if val.startswith("[") and val.endswith("]"):
                val = [v.strip().strip('"').strip("'") for v in val[1:-1].split(",")]
            fm[key] = val
    return fm


def classify_article(path: Path) -> tuple[str, list[str]]:
    """Classify an article as 'global', 'project', or 'ambiguous'.

    Returns (scope, matching_tags).
    """
    content = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(content)

    # Explicit scope override in frontmatter
    scope = fm.get("scope", "")
    if isinstance(scope, str) and scope in ("global", "project"):
        return scope, []

    tags = fm.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]
    tags_set = set(t.lower() for t in tags)

    project_matches = tags_set & PROJECT_TAGS
    if project_matches:
        return "project", list(project_matches)

    global_matches = tags_set & GLOBAL_TAGS
    if global_matches:
        return "global", list(global_matches)

    return "ambiguous", list(tags_set)


def list_local_articles() -> list[Path]:
    """List all articles in the local KB."""
    articles = []
    for subdir in ["concepts", "connections", "qa"]:
        d = KNOWLEDGE_DIR / subdir
        if d.exists():
            articles.extend(sorted(d.glob("*.md")))
    return articles


def read_index_entries(index_path: Path) -> dict[str, str]:
    """Read index.md and return {article_link: full_line}."""
    entries = {}
    if not index_path.exists():
        return entries
    for line in index_path.read_text(encoding="utf-8").splitlines():
        match = re.search(r"\[\[([^\]]+)\]\]", line)
        if match:
            entries[match.group(1)] = line
    return entries


def promote_article(article_path: Path, dry_run: bool = False) -> str | None:
    """Copy an article to the global KB. Returns the relative path or None."""
    rel = article_path.relative_to(KNOWLEDGE_DIR)
    dest = GLOBAL_KNOWLEDGE_DIR / rel

    if dry_run:
        return str(rel)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(article_path, dest)
    return str(rel)


def update_global_index(promoted: list[tuple[Path, str]]) -> None:
    """Add promoted articles to the global index."""
    local_index = read_index_entries(KNOWLEDGE_DIR / "index.md")
    global_index_content = GLOBAL_INDEX_FILE.read_text(encoding="utf-8")
    existing_global = read_index_entries(GLOBAL_INDEX_FILE)

    new_lines = []
    for article_path, rel_str in promoted:
        link_key = rel_str.replace(".md", "")
        if link_key in existing_global:
            # Update existing entry
            global_index_content = global_index_content.replace(
                existing_global[link_key],
                local_index.get(link_key, existing_global[link_key])
            )
        elif link_key in local_index:
            new_lines.append(local_index[link_key])

    if new_lines:
        global_index_content = global_index_content.rstrip() + "\n" + "\n".join(new_lines) + "\n"

    GLOBAL_INDEX_FILE.write_text(global_index_content, encoding="utf-8")


def update_global_log(promoted: list[tuple[Path, str]]) -> None:
    """Append promotion entry to global build log."""
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    articles_list = ", ".join(f"[[{rel.replace('.md', '')}]]" for _, rel in promoted)

    entry = (
        f"\n## [{now}] promote | from project KB\n"
        f"- Articles promoted: {articles_list}\n"
        f"- Source project: {ROOT_DIR.parent.name}\n"
    )

    with open(GLOBAL_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry)


def git_commit_and_push(message: str) -> bool:
    """Commit changes to global KB and push."""
    try:
        subprocess.run(["git", "add", "-A"], cwd=str(GLOBAL_KB_DIR), check=True,
                       capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(GLOBAL_KB_DIR), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "push", "-q"],
            cwd=str(GLOBAL_KB_DIR), check=True, capture_output=True, timeout=15,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"  Git warning: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Promote tech articles to global KB")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be promoted")
    parser.add_argument("--list", action="store_true", help="List articles with scope classification")
    parser.add_argument("--force", type=str, help="Force-promote a specific article by filename")
    args = parser.parse_args()

    if not GLOBAL_KB_DIR.exists():
        print(f"Global KB not found at {GLOBAL_KB_DIR}")
        print("Clone it first: git clone <repo> ~/.claude/knowledge-base")
        sys.exit(1)

    articles = list_local_articles()
    if not articles:
        print("No local articles found.")
        return

    if args.list:
        print(f"{'Scope':<10} {'Article':<45} {'Matching Tags'}")
        print("-" * 80)
        for a in articles:
            scope, tags = classify_article(a)
            rel = a.relative_to(KNOWLEDGE_DIR)
            tag_str = ", ".join(tags[:5]) if tags else "-"
            marker = "*" if scope == "global" else " "
            print(f"{marker}{scope:<9} {str(rel):<45} {tag_str}")
        return

    # Determine what to promote
    to_promote: list[Path] = []

    if args.force:
        match = [a for a in articles if args.force in a.name]
        if not match:
            print(f"No article matching '{args.force}' found.")
            sys.exit(1)
        to_promote = match
    else:
        for a in articles:
            scope, _ = classify_article(a)
            if scope == "global":
                to_promote.append(a)

    if not to_promote:
        print("No articles eligible for promotion.")
        return

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Articles to promote ({len(to_promote)}):")
    promoted = []
    for a in to_promote:
        rel = promote_article(a, dry_run=args.dry_run)
        if rel:
            print(f"  -> {rel}")
            promoted.append((a, rel))

    if args.dry_run:
        return

    # Update global index and log
    update_global_index(promoted)
    update_global_log(promoted)

    print(f"\nPromoted {len(promoted)} articles to {GLOBAL_KB_DIR}")

    # Commit and push
    article_names = ", ".join(Path(r).stem for _, r in promoted)
    message = f"promote: {article_names} from {ROOT_DIR.parent.name}"
    if git_commit_and_push(message):
        print("Committed and pushed to remote.")
    else:
        print("Changes saved locally. Push manually if needed.")


if __name__ == "__main__":
    main()
