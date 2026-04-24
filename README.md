# LLM Personal Knowledge Base

**Your AI conversations compile themselves into a searchable knowledge base.**

Adapted from [Karpathy's LLM Knowledge Base](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) architecture, but instead of clipping web articles, the raw data is your own conversations with Claude Code. When a session ends (or auto-compacts mid-session), Claude Code hooks capture the conversation transcript and spawn a background process that uses the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk) to extract the important stuff — decisions, lessons learned, patterns, gotchas — and appends it to a daily log. You then compile those daily logs into structured, cross-referenced knowledge articles organized by concept. Retrieval uses a simple index file instead of RAG — no vector database, no embeddings, just markdown.

Anthropic has clarified that personal use of the Claude Agent SDK is covered under your existing Claude subscription (Max, Team, or Enterprise) — no separate API credits needed. Unlike OpenClaw, which requires API billing for its memory flush, this runs on your subscription.

This repo is the **per-project compiler**. It pairs with the cross-machine [`claude-knowledge-base`](https://github.com/jbrianfrancis-ir/claude-knowledge-base) repo, which stores global tech/framework knowledge.

## Install

See **[claude-knowledge-base/INSTALL.md](https://github.com/jbrianfrancis-ir/claude-knowledge-base/blob/main/INSTALL.md)** for the authoritative step-by-step install guide (global KB setup, per-project clone, hook wiring, verification).

Hook wiring uses the template file shipped at the root of this repo:

```bash
cp claude-memory-compiler/settings.integrated.example.json .claude/settings.json
```

The `.claude/settings.json` inside this repo is only for standalone use (running Claude Code from the compiler dir itself, e.g. for testing). Don't copy that one into your project.

Quick version for an AI coding agent:

> "Clone https://github.com/jbrianfrancis-ir/claude-memory-compiler into this project. Follow INSTALL.md in https://github.com/jbrianfrancis-ir/claude-knowledge-base to wire the hooks. Then run `uv run --directory claude-memory-compiler python scripts/check_install.py` to verify."

## Verify an install

```bash
uv run --directory claude-memory-compiler python scripts/check_install.py
```

Twelve checks covering `uv`, Python, deps, hook wiring, global KB state, credentials, and that `session-start.py` actually emits valid JSON with both indexes. Exits non-zero on failure.

## How It Works

```
Conversation -> SessionEnd/PreCompact hooks -> flush.py extracts knowledge
    -> daily/YYYY-MM-DD.md -> compile.py -> knowledge/concepts/, connections/, qa/
        -> SessionStart hook pulls global KB, injects both indexes -> cycle repeats
```

- **Hooks** capture conversations automatically (session end + pre-compaction safety net)
- **flush.py** calls the Claude Agent SDK to decide what's worth saving, and after 6 PM triggers end-of-day compilation automatically
- **compile.py** turns daily logs into organized concept articles with cross-references
- **query.py** answers questions using index-guided retrieval (no RAG needed at personal scale)
- **lint.py** runs 7 health checks (broken links, orphans, contradictions, staleness)
- **seed.py** bootstraps the KB from existing project docs / planning artifacts
- **promote.py** copies tech/framework articles to the global KB
- **check_install.py** verifies the install

## Key Commands

All commands assume the compiler is cloned as a subdir of the project (`<project>/claude-memory-compiler/`). Run from the project root:

```bash
uv run --directory claude-memory-compiler python scripts/check_install.py    # verify install
uv run --directory claude-memory-compiler python scripts/compile.py          # compile daily logs
uv run --directory claude-memory-compiler python scripts/query.py "question" # ask the KB
uv run --directory claude-memory-compiler python scripts/lint.py             # health checks
uv run --directory claude-memory-compiler python scripts/lint.py --structural-only
uv run --directory claude-memory-compiler python scripts/seed.py             # seed from docs
uv run --directory claude-memory-compiler python scripts/promote.py          # promote to global
```

## Why No RAG?

Karpathy's insight: at personal scale (50-500 articles), the LLM reading a structured `index.md` outperforms vector similarity. The LLM understands what you're really asking; cosine similarity just finds similar words. RAG becomes necessary at ~2,000+ articles when the index exceeds the context window.

## Technical Reference

See **[AGENTS.md](AGENTS.md)** for the complete technical reference: article formats, hook architecture, script internals, cross-platform details, costs, and customization options. AGENTS.md is designed to give an AI agent everything it needs to understand, modify, or rebuild the system.
