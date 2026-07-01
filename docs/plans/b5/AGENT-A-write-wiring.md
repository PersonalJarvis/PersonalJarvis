# Agent A — `write-wiring`

> **Read `00-OVERVIEW.md` first.** This file only contains what is specific to your work. The shared contracts in §3, the global anti-patterns in §4, and the operational rules in §5 of the overview apply to you and are not duplicated here.

---

## 1. Mission

Make Jarvis write to the wiki **automatically** when a work session ends. Today the `SessionRollupWorker` (B7) and `WikiCurator` (B1) both exist and both work, but nothing connects them to the running app. Your job is the connector and the bootstrap call that turns them on at start-up.

After you ship: starting the desktop app launches a background subscription on `IdleEntered`; when the user goes idle for the configured threshold, the rollup worker produces a session digest, hands it off (eventually via Agent D's `CuratorScheduler`) to the curator, which writes one or more pages into the Obsidian vault.

---

## 2. Definition of Done

1. **All tests in `tests/unit/memory/wiki/test_integration.py` (new, by you) pass.**
2. **Pre-flight unit-test suite stays at its baseline failure count** — see §6 of the overview.
3. **Live voice demo (executed by the review agent in Wave 2):** speaking "Hey Jarvis, merk dir bitte: Alex wurde 1985 geboren" <!-- i18n-allow: live voice-demo utterance, real speech input example --> and waiting for idle produces a new page under `wiki/obsidian-vault/` containing the phrase "1985". This succeeds even when Agent D's work is **not yet merged**, by falling back to direct `WikiCurator.ingest` calls.
4. **No regression in `python -m jarvis.ui.web.launcher --headless`** — the desktop app boots cleanly.

---

## 3. Files you may touch

Create:
- `jarvis/memory/wiki/integration.py`
- `tests/unit/memory/wiki/test_integration.py`
- `tests/integration/memory/wiki/test_session_to_wiki_e2e.py` (smoke)

Modify:
- `jarvis/memory/wiki/__init__.py` — export `bootstrap_wiki_integration`, `WikiIntegrationHandle`.
- `jarvis/core/config.py` — add `WikiIntegrationConfig` Pydantic model (see §5 below) and wire it into `JarvisConfig` under a new top-level field `wiki_integration`.
- `jarvis/ui/web/server.py` — call `await bootstrap_wiki_integration(...)` inside the existing app-start hook; store the handle on `app.state`; call `await handle.shutdown()` from the existing shutdown hook.
- `jarvis.toml` — append a `[wiki_integration]` section with documented defaults.

## 4. Files you must NOT touch (Taboo)

- `jarvis/memory/wiki/curator.py`, `curator_llm.py`, `session_rollup.py`, `atomic_writer.py`, `backup.py`, `vault_index.py`, `log_writer.py`, `page.py`, `prompt.py`, `wikilink.py`, `protocols.py`, `cli.py` — **all of B1+B7 is read-only for you.** If you find a bug there, document it in the report, do not fix it.
- `jarvis/memory/wiki/search.py` — Agent B's territory.
- `jarvis/memory/wiki/scheduler.py`, `lock.py` — Agent D's territory; expect they may not exist yet.
- `jarvis/plugins/tool/wiki_recall.py` — Agent B.
- `jarvis/brain/wiki_context.py`, `jarvis/brain/manager.py`, `jarvis/brain/factory.py` — Agent C / Agent B.
- Anything in `jarvis/speech/`, `jarvis/skills/`, `jarvis/missions/`, `jarvis/audio/`, `jarvis/vision/` — out of scope.

## 5. Configuration model

Add to `jarvis/core/config.py`:

```python
class WikiIntegrationConfig(BaseModel):
    enabled: bool = True
    vault_root: Path = Path("wiki/obsidian-vault")
    subscribe_idle: bool = True              # listen for IdleEntered
    fallback_to_direct_ingest: bool = True   # when scheduler is missing
```

And in `JarvisConfig`:

```python
wiki_integration: WikiIntegrationConfig = Field(default_factory=WikiIntegrationConfig)
```

Update `jarvis.toml`:

```toml
[wiki_integration]
enabled = true
vault_root = "wiki/obsidian-vault"
subscribe_idle = true
fallback_to_direct_ingest = true
```

## 6. Interfaces you must provide

The exact signature is locked in overview §3.2. Below is the full implementation contract.

```python
# jarvis/memory/wiki/integration.py

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus
    from jarvis.core.config import WikiIntegrationConfig
    from jarvis.memory.wiki.protocols import PageRepository
    from jarvis.memory.wiki.scheduler import CuratorScheduler

log = logging.getLogger(__name__)


@dataclass
class WikiIntegrationHandle:
    """Returned by bootstrap. Caller invokes shutdown() at app teardown."""
    _unsubscribe_idle: Callable[[], None]
    _task: asyncio.Task | None

    async def shutdown(self) -> None: ...


async def bootstrap_wiki_integration(
    *,
    bus: "EventBus",
    repo: "PageRepository",
    vault_root: Path,
    config: "WikiIntegrationConfig",
    brain_caller: Callable[[str, str], Awaitable[str]] | None = None,
    scheduler_factory: Callable[..., "CuratorScheduler"] | None = None,
) -> WikiIntegrationHandle:
    """Wire SessionRollupWorker → (Scheduler →) WikiCurator and subscribe to IdleEntered.

    When `scheduler_factory` is None or `config.fallback_to_direct_ingest`
    is True and a TypeError occurs constructing the scheduler, fall back
    to calling WikiCurator.ingest() directly (bypassing the lock + cooldown).

    Returns a handle whose shutdown() unsubscribes and cancels any running task.
    """
```

## 7. Interfaces you consume

Existing — do not redefine:

| What | Where (exact path) | How you use it |
|------|--------------------|----------------|
| `SessionRollupWorker` class | `jarvis/memory/wiki/session_rollup.py` | Construct in bootstrap, call `.start()` and `.stop()`. It already subscribes itself to `IdleEntered` if given a bus — you just give it the bus. |
| `WikiCurator` class | `jarvis/memory/wiki/curator.py` | `await curator.ingest(text=…, source=…)`. Source string convention: `"session:<short-id>"`. |
| `PageRepository` protocol | `jarvis/memory/wiki/protocols.py` | Construct concrete repo via existing factory in `jarvis/memory/wiki/__init__.py`. |
| `IdleEntered` event | `jarvis/core/events.py` | Subscribe via `bus.subscribe(IdleEntered, handler)`; unsubscribe in shutdown. |
| `BrainProviderRegistry` | `jarvis/brain/provider_registry.py` | If `brain_caller` is None, construct a default via the registry using `cfg.brain.primary`. |

Optional, may not exist yet:

| What | Where | Behaviour if missing |
|------|-------|----------------------|
| `CuratorScheduler` | `jarvis/memory/wiki/scheduler.py` (Agent D) | If `scheduler_factory is None`: bypass it, call curator directly. Log a single `INFO` line "scheduler not wired, using direct ingest". |
| `VaultLock` | `jarvis/memory/wiki/lock.py` (Agent D) | Not touched by you. |

## 8. Anti-patterns specific to your scope

- **Don't subscribe to `IdleEntered` twice.** `SessionRollupWorker.start()` already subscribes when given a bus; if you also subscribe in `bootstrap_wiki_integration`, both fire and you get double-rollups.
- **Don't construct your own `EventBus`** — use the bus passed in.
- **Don't `await` the brain inside the IdleEntered handler synchronously.** The rollup worker already does the slow brain call; your handler just kicks it off and returns immediately. Use `asyncio.create_task(worker.flush_session(...))`.
- **Don't block the shutdown hook longer than 5 seconds.** If the running rollup task hasn't finished by then, cancel it and log a warning.
- **Don't read or write `wiki/obsidian-vault/` outside of going through the curator's atomic writer.** AP-3 of the overview.

## 9. Pre-flight test gate

```powershell
cd <USER_HOME>\Desktop\jarvis-b5-agent-A
pip install -e . --no-deps
python -m pytest tests/unit/ -q --tb=no > pre-flight.log
```

Save `pre-flight.log` to compare at the end.

## 10. Post-flight verification commands

Run each, copy command + output verbatim into the closing report.

```powershell
# 1. Unit tests pass for your new module
python -m pytest tests/unit/memory/wiki/test_integration.py -v
# Expected: all your new tests PASSED, no errors

# 2. Full unit suite, diff against pre-flight
python -m pytest tests/unit/ -q --tb=no > post-flight.log
fc /n pre-flight.log post-flight.log
# Expected: no new FAILED lines

# 3. Integration smoke
python -m pytest tests/integration/memory/wiki/test_session_to_wiki_e2e.py -v
# Expected: PASSED

# 4. Desktop app boots without your new code crashing it
python -m jarvis.ui.web.launcher --headless &
Start-Sleep -Seconds 8
Stop-Process -Name pythonw -Force -ErrorAction SilentlyContinue
Get-Content data/jarvis_desktop.log -Tail 30
# Expected: log shows "bootstrap_wiki_integration" succeeded; no traceback
```

## 11. Closing report

Free-form Markdown, but the **final line must be exactly** one of:

```
Goal fulfilled: yes — Reason: <one sentence>
Goal fulfilled: no — Reason: <one sentence>
```

Recommended sections (free-form, the order is yours):

- **Files changed** — bullet list.
- **What I did** — 4-8 bullets, "why" not "what".
- **Assumptions made** — only if any.
- **Verification** — paste the four command outputs from §10.
- **Open follow-ups** — anything you noticed but did not fix.
- The mandatory closing line.

## 12. Worktree setup recap

```powershell
cd <USER_HOME>\Desktop\Personal Jarvis
git worktree add -b impl/b5-agent-A <USER_HOME>\Desktop\jarvis-b5-agent-A impl/b5-base
cd <USER_HOME>\Desktop\jarvis-b5-agent-A
pip install -e . --no-deps
```

Then work entirely in `<USER_HOME>\Desktop\jarvis-b5-agent-A\`. Never `cd` back to the main repo until you're done.

Final commit (in the worktree):

```powershell
git add -A
git commit -m "feat(memory/wiki/b5/a): add bootstrap_wiki_integration + IdleEntered subscription"
```

Do **not** push.
