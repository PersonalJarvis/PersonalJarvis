# Agent B — `recall-tool`

> **Read `00-OVERVIEW.md` first.** This file only contains what is specific to your work. The shared contracts in §3, the global anti-patterns in §4, and the operational rules in §5 of the overview apply to you and are not duplicated here.

---

## 1. Mission

Give the router brain a way to **look things up in the long-term wiki**. Today the wiki is a passive folder — nothing in the running system can read it. You add two things:

1. A reusable in-process **search engine** (`VaultSearch`) that walks the Obsidian vault, scores keyword matches against frontmatter + body, and returns ranked hits.
2. A **`wiki-recall` plugin tool** that exposes the search to the router brain, registered as an entry point and listed in `ROUTER_TOOLS`.

The search engine is also consumed by Agent C for context-injection, which is why its signature is locked in the overview §3.1 — do not deviate.

---

## 2. Definition of Done

1. **Unit tests for `VaultSearch`** (in `tests/unit/memory/test_wiki_search.py`) and for **`WikiRecallTool`** (in `tests/unit/plugins/test_wiki_recall_tool.py`) all pass.
2. **Pre-flight unit-test suite stays at its baseline failure count** — see §6 of the overview.
3. **`python -m jarvis --plugins | findstr wiki-recall`** lists the tool.
4. **Live voice demo (executed by review agent in Wave 2):** with a wiki note titled `Alex.md` containing the text "born 1985", saying "Hey Jarvis, what year was Alex born?" triggers a `wiki-recall` call (visible in `data/jarvis_desktop.log`) and the brain answers with "1985" present in the TTS output.
5. **Tool latency p95 < 300 ms** for a vault with 100 notes (measured by your own smoke test).

---

## 3. Files you may touch

Create:
- `jarvis/memory/wiki/search.py`
- `jarvis/plugins/tool/wiki_recall.py`
- `tests/unit/memory/test_wiki_search.py`
- `tests/unit/plugins/test_wiki_recall_tool.py`
- `tests/integration/test_wiki_recall_e2e.py`

Modify:
- `pyproject.toml` — add one entry point line under `[project.entry-points."jarvis.tool"]`:
  ```toml
  wiki-recall = "jarvis.plugins.tool.wiki_recall:WikiRecallTool"
  ```
- `jarvis/brain/factory.py` — add `"wiki-recall"` to the `ROUTER_TOOLS` frozenset and add a tool-construction branch right after the existing `awareness-recall` branch (line will shift; locate by string search for `"awareness-recall"`).

## 4. Files you must NOT touch (Taboo)

- `jarvis/memory/wiki/curator.py`, `session_rollup.py`, `atomic_writer.py`, `backup.py`, `vault_index.py`, `log_writer.py`, `page.py`, `prompt.py`, `wikilink.py`, `protocols.py`, `cli.py`, `curator_llm.py` — all of B1+B7 is read-only.
- `jarvis/memory/wiki/integration.py`, `scheduler.py`, `lock.py` — Agent A / Agent D territory; expect they may not exist yet.
- `jarvis/brain/wiki_context.py`, `jarvis/brain/manager.py` — Agent C.
- Anything in `jarvis/speech/`, `jarvis/skills/`, `jarvis/missions/`, `jarvis/audio/`, `jarvis/vision/`.

## 5. Interfaces you must provide

The exact signatures are locked in overview §3.1 (`VaultSearch`, `SearchHit`) and §3.4 (`WikiRecallTool`). Below is the full implementation contract — read these closely, Agent C builds against them.

### 5.1 `jarvis/memory/wiki/search.py`

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SearchHit:
    title: str
    path: Path
    snippet: str
    score: float


class VaultSearch:
    """File-walking search over an Obsidian vault.

    Implementation notes the next agent may rely on:

    * Scans `vault_root` recursively for ``*.md``; skips directories whose
      name starts with ``.`` (notably ``.obsidian``).
    * Parses YAML frontmatter when present; frontmatter values are
      searchable but indexed under their key (a query that hits
      ``tags: project`` ranks above a body match).
    * Tokenizes the query on whitespace; case-insensitive; treats every
      token as OR but multi-token hits in the same page rank higher.
    * Snippet building: take the first 240 characters around the first
      match in the body, collapse whitespace, ASCII-trim word boundaries.
    * Returns ``[]`` (does not raise) when ``vault_root`` does not exist
      or contains no ``*.md``.
    * Caches the file list with mtime check on a single instance so a
      hot context-injection path does not re-walk the directory every
      call (target p95 < 50 ms after warm-up for 100 notes).
    """

    def __init__(self, vault_root: Path) -> None: ...

    def search(self, query: str, *, k: int = 5) -> list[SearchHit]: ...
```

### 5.2 `jarvis/plugins/tool/wiki_recall.py`

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from jarvis.core.protocols import ToolResult


class WikiRecallTool:
    name: str = "wiki-recall"
    description: str = (
        "Search the user's long-term Obsidian wiki for notes matching keywords. "
        "Returns a compact markdown summary with up to 5 ranked hits. Use this "
        "when the user asks 'what do we know about X', 'who is Y', or references "
        "a past project, person, or decision by name."
    )
    risk_tier: str = "safe"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "1-4 keywords; matched against frontmatter + body",
            },
            "k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
            },
        },
        "required": ["query"],
    }
    input_examples: list[dict[str, Any]] = [
        {"query": "Alex birthday"},
        {"query": "Personal Jarvis architecture"},
    ]

    def __init__(self, search: "VaultSearch") -> None: ...

    async def execute(self, args: dict[str, Any], ctx: object) -> ToolResult:
        """Render up to ``k`` SearchHits as a compact markdown block:

            ## Wiki hits for "<query>"
            - **<title>** — <snippet>… (path/relative/to/vault)
            - …

        Returns ``ToolResult(success=True, output=<markdown>)`` with the
        markdown ready to drop into the system prompt; ``success=False``
        with ``error="vault unavailable"`` when ``vault_root`` is missing.
        Empty hits yield a one-line "no matches" string with ``success=True``.
        """
```

## 6. Interfaces you consume

Existing:

| What | Where | How |
|------|-------|-----|
| `ToolResult` | `jarvis/core/protocols.py` | Return value of `execute`. |
| `ROUTER_TOOLS` frozenset | `jarvis/brain/factory.py:40` (approx) | Add `"wiki-recall"`. |
| Tool-construction branch pattern | `jarvis/brain/factory.py` — see the existing `awareness-recall` branch as your template | DI via constructor; the factory imports `VaultSearch` lazily and builds an instance with `vault_root = cfg.wiki_integration.vault_root` (Agent A defines that config field; assume it exists). |

If `cfg.wiki_integration` is missing at construction time, default to `Path("wiki/obsidian-vault")` and log a single `WARNING` line. This is the **plausible-assumption fallback** in action.

## 7. Anti-patterns specific to your scope

- **Don't call out to an LLM inside `VaultSearch.search`.** This must be pure file-system + regex. Latency is in the voice critical path.
- **Don't add new dependencies** (no `whoosh`, `tantivy`, etc.). Plain `pathlib` + `re` + optional `yaml` (already in deps).
- **Don't index `.obsidian/`** — those are config files, not content.
- **Don't return paths absolute in `ToolResult.output`** — render them relative to `vault_root` so the brain sees `10-notes/Alex.md`, not `C:\Users\...\Alex.md`.
- **Don't fail on a malformed YAML frontmatter** — skip the frontmatter, search the rest, log a single `DEBUG` line.
- **Don't add `"wiki-recall"` to a `SUB_TOOLS` frozenset.** The router tier is the only one that gets self-modification-class tools. Recall is router-tier-only.
- **Don't echo the user's query into log lines at INFO level** — privacy. Use `DEBUG` for the query, `INFO` for hit-count summary.

## 8. Pre-flight test gate

```powershell
cd <USER_HOME>\Desktop\jarvis-b5-agent-B
pip install -e . --no-deps
python -m pytest tests/unit/ -q --tb=no > pre-flight.log
```

## 9. Post-flight verification commands

```powershell
# 1. Unit tests for your two modules
python -m pytest tests/unit/memory/test_wiki_search.py tests/unit/plugins/test_wiki_recall_tool.py -v
# Expected: all PASSED

# 2. Full unit suite diff
python -m pytest tests/unit/ -q --tb=no > post-flight.log
fc /n pre-flight.log post-flight.log
# Expected: no new FAILED lines

# 3. Entry point registered
python -m jarvis --plugins | findstr wiki-recall
# Expected: a line containing "wiki-recall"

# 4. Integration: tool registers and returns hits against the actual obsidian-vault
python -m pytest tests/integration/test_wiki_recall_e2e.py -v
# Expected: PASSED

# 5. Latency smoke (your own test)
python -m pytest tests/unit/memory/test_wiki_search.py -v -k latency
# Expected: PASSED, with the printed p95 well under 300 ms
```

## 10. Closing report

Free-form Markdown, final line **must be exactly** one of:

```
Goal fulfilled: yes — Reason: <one sentence>
Goal fulfilled: no — Reason: <one sentence>
```

Recommended sections (order is yours): Files changed · What I did · Assumptions made · Verification (paste the §9 outputs) · Open follow-ups · the mandatory line.

## 11. Worktree setup recap

```powershell
cd <USER_HOME>\Desktop\Personal Jarvis
git worktree add -b impl/b5-agent-B <USER_HOME>\Desktop\jarvis-b5-agent-B impl/b5-base
cd <USER_HOME>\Desktop\jarvis-b5-agent-B
pip install -e . --no-deps
```

Final commit:

```powershell
git add -A
git commit -m "feat(memory/wiki/b5/b): add VaultSearch + wiki-recall router tool"
```

Do **not** push.
