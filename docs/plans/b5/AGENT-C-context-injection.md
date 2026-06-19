# Agent C — `context-injection`

> **Read `00-OVERVIEW.md` first.** This file only contains what is specific to your work. The shared contracts in §3, the global anti-patterns in §4, and the operational rules in §5 of the overview apply to you and are not duplicated here.

---

## 1. Mission

The brain should know what's in the wiki **even when it doesn't think to call the recall tool**. Your job is the silent safety net: before every brain turn, you extract one to three keywords from the user's utterance, run a fast vault search via Agent B's `VaultSearch`, and prepend any hits to the system prompt as a short context block.

Latency is sacred here. The voice path budget is ~1.5 s p95 for the whole turn. Your injection must finish in **≤ 80 ms** or it skips itself silently and the turn proceeds without the wiki context. Wrong answer beats slow answer.

---

## 2. Definition of Done

1. **Unit tests for `WikiContextInjector` pass**, including the latency-budget short-circuit, the no-keyword case, the no-hits case, and the prompt-augmentation case.
2. **Pre-flight unit-test suite stays at baseline failure count.**
3. **Live voice demo (Wave 2):** with the same `Alex.md` note as Agent B's demo, asking "Was weißt du über Alex?" produces a brain answer that includes "1985", **even if `wiki-recall` is not explicitly called by the brain**. Verified by checking `data/jarvis_desktop.log` for a `WikiContextInjector injected=` line right before the brain call.
4. **No measurable regression in tier-1 voice latency** — your hook adds < 20 ms p50 to `BrainManager.generate` when vault has 5 notes.

---

## 3. Files you may touch

Create:
- `jarvis/brain/wiki_context.py`
- `tests/unit/brain/test_wiki_context.py`
- `tests/integration/test_brain_with_wiki_context.py`

Modify:
- `jarvis/brain/manager.py` — add a single hook just before the brain call inside `BrainManager.generate()`. Locate by searching for the comment line that precedes the actual provider call (it varies by manager version; do not assume a line number). The hook is one `await injector.maybe_inject(...)` call, gated by a constructor-injected optional `WikiContextInjector`.
- `jarvis/brain/factory.py` — construct the injector once during brain build-up and pass it into `BrainManager`. Only at `tier == "router"`; do not pass it for any sub-tier.
- `jarvis/core/config.py` — add a small `WikiContextConfig` Pydantic model nested under `wiki_integration` (Agent A defines `WikiIntegrationConfig`; you only add this nested field if missing — see §5 below for assumption-handling).

## 4. Files you must NOT touch (Taboo)

- All wiki backing modules: `jarvis/memory/wiki/curator.py`, `session_rollup.py`, `search.py` (Agent B), `integration.py` (Agent A), `scheduler.py`, `lock.py` (Agent D), and the entire B1+B7 surface (`atomic_writer.py`, `backup.py`, `vault_index.py`, etc.).
- `jarvis/plugins/tool/wiki_recall.py` — Agent B.
- Anything in `jarvis/speech/`, `jarvis/skills/`, `jarvis/missions/`, `jarvis/audio/`, `jarvis/vision/`.
- `jarvis/brain/router.py`, `jarvis/brain/output_filter.py` — out of scope.

## 5. Interfaces you must provide

Signature is locked in overview §3.5. Full implementation contract:

```python
# jarvis/brain/wiki_context.py

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.memory.wiki.search import VaultSearch

log = logging.getLogger(__name__)


class WikiContextInjector:
    """Latency-bounded wiki-snippet injector for the system prompt.

    Construction is cheap; one instance is reused for the lifetime of the
    BrainManager. The injector is a no-op when ``search`` is None (used
    when Agent B's work is not yet merged — fallback path).
    """

    def __init__(
        self,
        *,
        search: "VaultSearch | None",
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
        """Returns ``system_prompt`` unchanged on any of:

        * ``search is None``
        * no extractable keywords from ``user_text``
        * ``VaultSearch.search`` exceeds ``latency_budget_ms``
        * search returns zero hits

        Otherwise returns ``system_prompt + "\\n\\n## Wiki context\\n" + …``
        with up to ``max_chars`` of merged snippets, each prefixed by its
        page title.

        Logs exactly one line per call at INFO:
            ``WikiContextInjector injected=<bool> hits=<n> latency_ms=<int>``
        """
```

### Keyword extraction (your own helper)

You decide the heuristic. A starting point:

- Tokenize `user_text` on whitespace + simple punctuation.
- Drop tokens shorter than `min_keyword_length` (default 4 chars).
- Drop a small German + English stopword list (~30 entries — keep it inlined, no nltk).
- Keep proper nouns (capitalized inside the sentence, not at start).
- Cap at the first three remaining tokens.

If extraction yields zero tokens, short-circuit and return the unchanged prompt.

### Configuration

Add to `jarvis/core/config.py` under `WikiIntegrationConfig` (Agent A's model — if it does not exist yet in your worktree, **add a separate top-level `WikiContextConfig`** in the same file and document it as your plausible-assumption fallback):

```python
class WikiContextConfig(BaseModel):
    enabled: bool = True
    max_chars: int = 1500
    latency_budget_ms: int = 80
    min_keyword_length: int = 4
```

## 6. Interfaces you consume

| What | Where | How |
|------|-------|-----|
| `VaultSearch` | `jarvis/memory/wiki/search.py` (Agent B) | Construct via factory; if module not present, instantiate the injector with `search=None`. |
| `BrainManager` | `jarvis/brain/manager.py` | Modify the existing `generate()` method to call the injector right before the provider call. |
| Factory | `jarvis/brain/factory.py` | Construct the injector once during `_build_router_brain` (locate by name). |

If `jarvis.memory.wiki.search` doesn't import (Agent B's branch not merged), the factory passes `search=None` and the injector silently does nothing. This is the **fallback** that lets Agent C ship before Agent B in Wave 2.

## 7. Anti-patterns specific to your scope

- **Don't await the search without `asyncio.wait_for`.** A single missing latency bound here breaks the whole voice path. Use `asyncio.wait_for(self._search.search(query, k=5), timeout=self._latency_budget_ms / 1000)`. Catch `asyncio.TimeoutError`, log + return unchanged prompt.
- **Don't inject if `user_text` is empty or only contains stopwords.** Empty injection is worse than no injection — the brain may hallucinate around an empty `## Wiki context` header.
- **Don't try to be clever with embeddings or LLM-based query expansion.** Stay regex/tokenization-only. If we want embeddings, that's a follow-up phase.
- **Don't add the injector for sub-tiers.** Only the router brain gets context-injection — sub-tiers exist for narrow tasks and don't benefit. Construct in `factory.py` inside the `tier == "router"` branch only.
- **Don't write to the wiki from inside `maybe_inject`.** Read-only path. AP-3 of the overview.
- **Don't use `print` for the injection log line.** Use the module logger. The voice path is noisy already.

## 8. Pre-flight test gate

```powershell
cd C:\Users\Administrator\Desktop\jarvis-b5-agent-C
pip install -e . --no-deps
python -m pytest tests/unit/ -q --tb=no > pre-flight.log
```

## 9. Post-flight verification commands

```powershell
# 1. Unit tests for the injector
python -m pytest tests/unit/brain/test_wiki_context.py -v
# Expected: all PASSED — at minimum: no-keyword, no-hits, timeout, hit-injection

# 2. Full unit suite diff
python -m pytest tests/unit/ -q --tb=no > post-flight.log
fc /n pre-flight.log post-flight.log
# Expected: no new FAILED lines

# 3. Integration: brain.generate runs with the injector hooked in
python -m pytest tests/integration/test_brain_with_wiki_context.py -v
# Expected: PASSED

# 4. Latency budget actually enforced
python -m pytest tests/unit/brain/test_wiki_context.py -v -k timeout
# Expected: PASSED — the timeout-path test must show the unchanged prompt is returned
```

## 10. Closing report

Free-form Markdown, final line **must be exactly**:

```
Goal erfüllt: ja — Grund: <one sentence>
Goal erfüllt: nein — Grund: <one sentence>
```

Recommended sections: Files changed · What I did · Assumptions made (especially around the stopword list and the keyword heuristic — these are judgment calls) · Verification (paste §9 outputs) · Open follow-ups · the mandatory line.

## 11. Worktree setup recap

```powershell
cd C:\Users\Administrator\Desktop\Personal Jarvis
git worktree add -b impl/b5-agent-C C:\Users\Administrator\Desktop\jarvis-b5-agent-C impl/b5-base
cd C:\Users\Administrator\Desktop\jarvis-b5-agent-C
pip install -e . --no-deps
```

Final commit:

```powershell
git add -A
git commit -m "feat(brain/b5/c): add WikiContextInjector + BrainManager hook"
```

Do **not** push.
