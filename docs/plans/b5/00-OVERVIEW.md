# B5 — Jarvis ↔ Wiki Integration: Overview

**Goal.** Make the long-term Obsidian wiki actively part of Jarvis: writes happen automatically at session end, reads happen automatically before the brain answers. End state: a voice turn like "Hey Jarvis, wann ist Alex geboren" returns the answer sourced from a wiki note that Jarvis previously wrote himself.

**This document is the single source of truth for B5.** All four parallel coding agents read this first, then their own `AGENT-X-*.md` briefing.

---

## 1. Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Voice turn: "Hey Jarvis, …"                             │
│                                                          │
│   ┌────────────────┐         ┌──────────────────┐       │
│   │ Brain (router) │ ──────▶ │ Tool: wiki-recall │ ←── Agent B
│   └───────┬────────┘         └────────┬─────────┘       │
│           │                           │                  │
│           │ context-injection         │                  │
│           ▼                           ▼                  │
│   ┌────────────────┐         ┌──────────────────┐       │
│   │ WikiContextInj │ ──────▶ │   VaultSearch    │ ←── Agent B
│   └────────────────┘         └──────────────────┘       │
│         ↑ Agent C                                        │
│                                                          │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  IdleEntered bus event                                   │
│                                                          │
│   ┌─────────────────────┐    ┌──────────────────┐       │
│   │ SessionRollupWorker │ ─▶ │  CuratorScheduler │ ←── Agent D
│   │  (B7, existing)     │    └────────┬─────────┘       │
│   └─────────────────────┘             │                  │
│                                       ▼                  │
│                              ┌──────────────────┐       │
│                              │  VaultLock       │ ←── Agent D
│                              └────────┬─────────┘       │
│                                       │                  │
│                                       ▼                  │
│                              ┌──────────────────┐       │
│                              │   WikiCurator     │       │
│                              │   (B1, existing)  │       │
│                              └──────────────────┘       │
│                                       ↑                  │
│            bootstrap_wiki_integration  ←── Agent A      │
└─────────────────────────────────────────────────────────┘
```

The whole stack runs on top of B0 (foundation), B1 (WikiCurator + supporting infrastructure) and B7 (SessionRollupWorker). B5 itself adds **wiring + retrieval + context-injection + scheduling**, not new wiki authoring logic.

---

## 2. The four agents

| Code | Name | Owns | One-sentence mission |
|------|------|------|----------------------|
| **A** | `write-wiring` | `jarvis/memory/wiki/integration.py` (new) + bootstrap hooks | Subscribe `IdleEntered`, drive `SessionRollupWorker` → `CuratorScheduler`, wire into `jarvis/ui/web/server.py` startup. |
| **B** | `recall-tool` | `jarvis/memory/wiki/search.py` + `jarvis/plugins/tool/wiki_recall.py` | Provide a `VaultSearch` engine (file-walking + frontmatter-aware grep) and a `wiki-recall` Router tool that exposes it to the brain. |
| **C** | `context-injection` | `jarvis/brain/wiki_context.py` (new) + hook in `manager.py` | Before each brain call, extract 1–3 keywords from user text, run `VaultSearch`, and prepend up to ~1500 chars of matched snippets to the system prompt. Latency-bounded. |
| **D** | `scheduler` | `jarvis/memory/wiki/scheduler.py` + `jarvis/memory/wiki/lock.py` (new) | Coordinate when the Curator actually runs: post-session trigger, optional periodic, file-based lock to prevent two parallel curator runs. |

Agents work in **parallel** from the same baseline branch (see §6). The Wave-2 step is owned by the review agent (me), not by the four agents.

---

## 3. Shared interface contracts (binding for all agents)

These signatures are **non-negotiable**. Any agent that deviates breaks the merge. If an agent thinks the signature is wrong, the agent's plausible-assumption rule (§5) does **not** apply — stop and flag in the report.

### 3.1 `VaultSearch` (owned by Agent B, consumed by Agent C)

```python
# jarvis/memory/wiki/search.py

from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True, slots=True)
class SearchHit:
    title: str                  # H1 of the matched page, falling back to filename
    path: Path                  # absolute path inside the vault
    snippet: str                # ≤ 240 chars, ASCII-trimmed around the match
    score: float                # 0.0 .. 1.0; agent decides scoring, monotonic

class VaultSearch:
    def __init__(self, vault_root: Path) -> None: ...

    def search(self, query: str, *, k: int = 5) -> list[SearchHit]:
        """Return up to k hits, highest score first.
        `query` may contain multiple whitespace-separated keywords;
        treat as OR (any match counts) but rank by hit count + recency.
        Returns [] (never raises) when vault_root is missing or empty."""
```

### 3.2 `bootstrap_wiki_integration` (owned by Agent A, called by `jarvis/ui/web/server.py`)

```python
# jarvis/memory/wiki/integration.py

from collections.abc import Awaitable, Callable
from pathlib import Path
from jarvis.core.bus import EventBus
from jarvis.core.config import WikiIntegrationConfig
from jarvis.memory.wiki.protocols import PageRepository

class WikiIntegrationHandle:
    async def shutdown(self) -> None: ...

async def bootstrap_wiki_integration(
    *,
    bus: EventBus,
    repo: PageRepository,
    vault_root: Path,
    config: WikiIntegrationConfig,
    brain_caller: Callable[[str, str], Awaitable[str]] | None = None,
    scheduler_factory: Callable[..., "CuratorScheduler"] | None = None,
) -> WikiIntegrationHandle: ...
```

`scheduler_factory` is `None` if Agent D's work is not yet merged — Agent A must fall back to calling `WikiCurator.ingest` directly in that case (see §7 Wave 2 for the actual wiring).

### 3.3 `CuratorScheduler` (owned by Agent D, called by Agent A in Wave 2)

```python
# jarvis/memory/wiki/scheduler.py

from enum import Enum
from dataclasses import dataclass
from jarvis.memory.wiki.curator import WikiCurator
from jarvis.memory.wiki.lock import VaultLock

class TriggerSource(str, Enum):
    SESSION_END = "session_end"
    PERIODIC = "periodic"
    MANUAL = "manual"

@dataclass(frozen=True, slots=True)
class SchedulerResult:
    triggered: bool                   # False when lock was held / cap hit
    skip_reason: str                  # "" when triggered; else "locked" | "cooldown" | …
    curator_output_label: str         # echoes WikiCurator's status string when triggered

class CuratorScheduler:
    def __init__(
        self,
        *,
        curator: WikiCurator,
        lock: VaultLock,
        config: "SchedulerConfig",
    ) -> None: ...

    async def trigger(
        self,
        source: TriggerSource,
        *,
        episode_paths: list[Path] | None = None,
    ) -> SchedulerResult: ...
```

### 3.4 `WikiRecallTool` (owned by Agent B, called by the brain via `ROUTER_TOOLS`)

```python
# jarvis/plugins/tool/wiki_recall.py

class WikiRecallTool:
    name: str = "wiki-recall"
    description: str = (
        "Search the user's long-term Obsidian wiki for notes matching keywords. "
        "Returns a compact markdown summary with up to 5 ranked hits. Use this "
        "when the user asks 'what do we know about X', 'who is Y', "
        "or references a past project, person, or decision by name."
    )
    risk_tier: str = "safe"
    schema: dict = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "1-4 keywords"},
            "k":     {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        },
        "required": ["query"],
    }
    input_examples: list[dict] = [
        {"query": "Alex birthday"},
        {"query": "Personal Jarvis architecture"},
    ]

    def __init__(self, search: "VaultSearch") -> None: ...
    async def execute(self, args: dict, ctx: object) -> "ToolResult": ...
```

### 3.5 `WikiContextInjector` (owned by Agent C, called from `BrainManager.generate`)

```python
# jarvis/brain/wiki_context.py

class WikiContextInjector:
    def __init__(
        self,
        *,
        search: "VaultSearch",
        max_chars: int = 1500,
        latency_budget_ms: int = 80,
        min_keyword_length: int = 4,
    ) -> None: ...

    async def maybe_inject(
        self,
        *,
        user_text: str,
        system_prompt: str,
    ) -> str:
        """Return system_prompt unchanged if no hits or latency budget blown.
        Otherwise return system_prompt with an appended ``## Wiki context``
        section containing up to max_chars of merged snippets."""
```

---

## 4. Global anti-patterns (apply to all four agents)

Each entry is a hard rule plus a one-line why. Violations are the most common reason a Wave-2 merge fails.

| # | Don't | Why |
|---|-------|-----|
| **AP-1** | Don't call the Anthropic API directly. | The user has no Anthropic account. Use `cfg.brain.primary` and the existing `BrainProviderRegistry`. |
| **AP-2** | Don't hardcode the brain model. | Multi-provider strategy is project policy. Read from config; fall back via the registry. |
| **AP-3** | Don't write to `wiki/obsidian-vault/` outside of the existing `AtomicWriter` path. | The atomic-writer is the only code path that handles backup, validation, rollback, and the 30s concurrent-edit skip. |
| **AP-4** | Don't add network calls inside the voice critical path without an explicit timeout. | Voice turns must stay under ~1.5 s p95. Anything blocking on IO needs `asyncio.wait_for`. |
| **AP-5** | Don't mock the database in integration tests. | BUG-008 came back twice because mocked tests hid drift between models. Use real SQLite or real fs. |
| **AP-6** | Don't introduce a new EventBus instance. | Use the bus provided through bootstrap; lateral communication must stay on the shared bus. |
| **AP-7** | Don't write user-facing strings in German into code (comments, docstrings, log messages, exceptions, prompt strings). | Project output-language policy: all generated artifacts are English. The user chat reply is independent. |
| **AP-8** | Don't catch and silently swallow exceptions in the curator or scheduler path. | BUG-003 (silent SAPI5 fallback) and the BUG-010 empty-TTS-buffer pattern both came from silent fallbacks. Log and re-raise or return a `*_failure` status string. |

---

## 5. Operational rules (apply to all four agents)

### 5.1 Worktree

Each agent works in **its own git worktree**, branched off `b5-base` (set up by the review agent before spawning — see §6). Path convention:

```
<USER_HOME>\Desktop\jarvis-b5-agent-A\
<USER_HOME>\Desktop\jarvis-b5-agent-B\
<USER_HOME>\Desktop\jarvis-b5-agent-C\
<USER_HOME>\Desktop\jarvis-b5-agent-D\
```

Worktree branch names: `impl/b5-agent-A`, `…B`, `…C`, `…D`.

The agent **must** run `pip install -e . --no-deps` in its worktree before any other action — otherwise editable-install pins to a stale clone (BUG-006, BUG-014 episode 2).

### 5.2 Commits

The agent commits **once** at the very end of its session, after all checks pass:

```
feat(memory/wiki/b5/<X>): <one-line summary>

<body — bullet list of what was added, max ~12 lines>
```

Example: `feat(memory/wiki/b5/a): add bootstrap_wiki_integration`. No intermediate commits. No `git push` — the review agent merges.

### 5.3 Plausible-assumption fallback

If something in this overview or the agent's own briefing is ambiguous:

1. Pick the most plausible interpretation.
2. Document it in the closing report under a section titled `## Assumptions made`, with one bullet per assumption.

**Exception:** if the ambiguity touches a §3 shared interface signature, the agent **stops and reports** instead of guessing. A wrong interface guess breaks the merge.

### 5.4 Pre-flight & post-flight test gate

The agent runs the full unit-test suite **twice** in its worktree:

```powershell
# Pre-flight — before any code change
python -m pytest tests/unit/ -q > pre-flight.log

# Post-flight — after all code is written
python -m pytest tests/unit/ -q > post-flight.log
```

If `post-flight.log` shows new failures that are not in `pre-flight.log`, the agent **does not commit** and reports the diff. Existing pre-flight failures are inherited from the baseline and not the agent's responsibility (see project state notes in §6).

### 5.5 Closing report

Free text but the **final line must be exactly one of**:

```
Goal fulfilled: yes — Reason: <one sentence>
Goal fulfilled: no — Reason: <one sentence>
```

No quotation marks, no markdown around it. The review agent greps for this line.

---

## 6. Baseline branch setup (review agent does this before spawning)

The four agent worktrees branch off `impl/b5-base`, which is constructed once:

```powershell
# in the main repo
git checkout impl/wiki-memory-b7              # has B0+B1+B7
git checkout -b impl/b5-base
git merge --no-ff main                        # picks up B2 obsidian-vault if committed
# resolve any conflicts (likely none — disjoint file sets)
```

Pre-existing test failures on `impl/b5-base` at the moment of branching are documented here:

```
tests/unit/audio/test_capture_device.py::test_auto_headset_prefers_wasapi_for_same_microphone_name
tests/unit/conductor/test_core.py::test_agent_anthropic_missing_binary
tests/unit/conductor/test_core.py::test_agent_anthropic_parses_json_output
tests/unit/test_router_delegator_policy.py::TestDelegatorPolicyInPrompt::test_wellbeing_smalltalk_is_not_status_filler
tests/unit/test_tier_defaults.py::TestTierDefaultsCatalog::test_router_models_look_fast
```

These five are **not** an agent's problem.

---

## 7. Wave 2 — final integration (review agent owns this)

After all four agents commit and report, the review agent:

1. Reads each report, verifies the mandatory closing line.
2. Reviews each diff against its briefing (file lists, anti-patterns, interfaces).
3. Merges the four branches into `impl/b5-base` in this order: **B → D → A → C** (B first because A and C consume its interfaces; D before A because A wires D in).
4. Resolves any merge conflicts in-place (per the meta-plan decision).
5. Runs the integration smoke test:

   ```powershell
   python -m pytest tests/integration/memory/wiki/ -q
   ```

6. Runs the live voice demo:
   - Launch desktop app: `Start-Process pythonw -ArgumentList "-m","jarvis.ui.web.launcher"`
   - Say: "Hey Jarvis, schreib in dein Wiki: Alex wurde 1985 geboren."  <!-- i18n-allow: live voice-demo utterance, real speech input example -->
   - Wait for `IdleEntered` (~2 minutes) or trigger `flush_session()` manually via the existing CLI.
   - Verify a new note appears under `wiki/obsidian-vault/10-notes/` mentioning Alex + 1985.
   - Say: "Hey Jarvis, wann ist Alex geboren?"  <!-- i18n-allow: live voice-demo utterance, real speech input example -->
   - Verify the TTS response references 1985, sourced from the wiki.

If the voice demo passes, B5 is done.

---

## 8. Definition of Done for B5 as a whole

- All four agent branches merged into `impl/b5-base` without breaking the §6 pre-flight test set.
- New integration tests under `tests/integration/memory/wiki/` are green.
- The §7 voice demo succeeds end-to-end on the live Jarvis instance.
- `docs/plans/b5/00-OVERVIEW.md` (this file) gets a final section appended titled `## Outcome` with one paragraph: what shipped, what was deferred, any follow-ups.
- The dashboard `jarvis-status-dashboard.html` gets B5 flipped to "done".
