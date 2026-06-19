# Agent D — `scheduler`

> **Read `00-OVERVIEW.md` first.** This file only contains what is specific to your work. The shared contracts in §3, the global anti-patterns in §4, and the operational rules in §5 of the overview apply to you and are not duplicated here.

---

## 1. Mission

The curator runs the LLM, atomic-write pipeline, backup-and-validate cycle — it's the most expensive operation in the wiki stack. Today nothing prevents two curator runs from racing against each other, and nothing throttles repeated triggers in a tight loop. Your job is the **traffic light**: a small scheduler with a file-based lock and a cool-down policy, sitting between the rest of the system and `WikiCurator.ingest`.

You also define when periodic runs happen (default: never — only `SESSION_END` triggers fire unless config opts in to periodic), so the system is calm by default and noisy only when explicitly configured.

---

## 2. Definition of Done

1. **Unit tests for `CuratorScheduler` and `VaultLock` pass.** Coverage must include: lock contention (two concurrent triggers — second one returns `skip_reason="locked"`), cooldown (two triggers within cooldown window — second one returns `skip_reason="cooldown"`), and the manual override (`TriggerSource.MANUAL` bypasses cooldown but never bypasses the lock).
2. **Pre-flight unit-test suite stays at baseline failure count.**
3. **Live voice demo (Wave 2):** firing two `session_end` triggers in rapid succession produces exactly one curator run; the second is logged as `skip_reason="cooldown"`. Verified by inspecting `data/jarvis_desktop.log`.
4. **Lock file is filesystem-portable** (uses `pathlib` + `os.O_EXCL` style, no Windows-only or Unix-only primitives) and **always cleaned up**, even on crash. Tested by killing the process mid-trigger and verifying the lock file is gone on next start-up.

---

## 3. Files you may touch

Create:
- `jarvis/memory/wiki/scheduler.py`
- `jarvis/memory/wiki/lock.py`
- `tests/unit/memory/wiki/test_scheduler.py`
- `tests/unit/memory/wiki/test_lock.py`

Modify:
- `jarvis/core/config.py` — add `SchedulerConfig` Pydantic model (see §5 below). Wire it into the existing `WikiIntegrationConfig` (Agent A's model — if it doesn't exist yet in your worktree, see §5 for the plausible-assumption fallback).
- `jarvis/memory/wiki/__init__.py` — export `CuratorScheduler`, `SchedulerConfig`, `SchedulerResult`, `TriggerSource`, `VaultLock`.

## 4. Files you must NOT touch (off-limits)

- `jarvis/memory/wiki/curator.py`, `session_rollup.py`, `atomic_writer.py`, `backup.py`, `vault_index.py`, `log_writer.py`, `page.py`, `prompt.py`, `wikilink.py`, `protocols.py`, `cli.py`, `curator_llm.py` — all of B1+B7 is read-only.
- `jarvis/memory/wiki/integration.py` — Agent A; you do not call into it, you provide a class it imports later in Wave 2.
- `jarvis/memory/wiki/search.py`, `jarvis/plugins/tool/wiki_recall.py`, `jarvis/brain/wiki_context.py`, `jarvis/brain/manager.py`, `jarvis/brain/factory.py` — Agent B / Agent C.
- Anything in `jarvis/speech/`, `jarvis/skills/`, `jarvis/missions/`, `jarvis/audio/`, `jarvis/vision/`, `jarvis/ui/`.

## 5. Configuration model

Add to `jarvis/core/config.py`:

```python
class SchedulerConfig(BaseModel):
    cooldown_seconds: int = 60                  # ignore trigger within N seconds of previous run
    enable_periodic: bool = False               # whether to run on a timer at all
    periodic_interval_minutes: int = 30
    lock_path: Path = Path("data/wiki_curator.lock")
    lock_stale_after_seconds: int = 300         # consider lock stale and steal after N seconds
```

Wire it: nest a field `scheduler: SchedulerConfig` inside `WikiIntegrationConfig`. **If `WikiIntegrationConfig` does not exist in your worktree yet** (Agent A's branch not merged): add `SchedulerConfig` as a top-level field on `JarvisConfig` instead, called `wiki_scheduler`, and document this as your plausible-assumption fallback. Agent A's branch will rebase its model on top.

`jarvis.toml` additions:

```toml
[wiki_integration.scheduler]
cooldown_seconds = 60
enable_periodic = false
periodic_interval_minutes = 30
lock_path = "data/wiki_curator.lock"
lock_stale_after_seconds = 300
```

## 6. Interfaces you must provide

Signatures are locked in overview §3.3. Full implementation contract:

### 6.1 `jarvis/memory/wiki/lock.py`

```python
from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)


class VaultLock:
    """File-based exclusive lock for curator runs.

    Uses ``open(path, "x")`` semantics so creation is atomic on every
    OS. Writes the current PID + a monotonic timestamp into the lock
    file so a stale lock (older than ``stale_after_seconds``) can be
    detected and stolen on next ``acquire``.

    Always usable as a context manager:

        with lock:
            ...
    """

    def __init__(self, path: Path, *, stale_after_seconds: int = 300) -> None: ...

    def acquire(self, *, timeout_s: float = 5.0) -> bool:
        """Block up to ``timeout_s`` waiting for the lock.
        Returns True when acquired, False when timed out.
        Steals a stale lock automatically."""

    def release(self) -> None: ...

    def __enter__(self) -> "VaultLock": ...
    def __exit__(self, exc_type, exc, tb) -> None: ...
```

### 6.2 `jarvis/memory/wiki/scheduler.py`

```python
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.core.config import SchedulerConfig
    from jarvis.memory.wiki.curator import WikiCurator
    from jarvis.memory.wiki.lock import VaultLock

log = logging.getLogger(__name__)


class TriggerSource(str, Enum):
    SESSION_END = "session_end"
    PERIODIC = "periodic"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class SchedulerResult:
    triggered: bool
    skip_reason: str
    curator_output_label: str


class CuratorScheduler:
    """Wraps ``WikiCurator`` with a lock + cooldown.

    Cooldown rules (in priority order):
        1. ``MANUAL`` triggers bypass cooldown but **not** the lock.
        2. ``SESSION_END`` honours cooldown.
        3. ``PERIODIC`` honours cooldown **and** is a no-op when
           ``config.enable_periodic`` is False.

    Logs exactly one line per ``trigger()`` call at INFO with the
    full ``SchedulerResult`` rendered as ``key=value``.
    """

    def __init__(
        self,
        *,
        curator: "WikiCurator",
        lock: "VaultLock",
        config: "SchedulerConfig",
    ) -> None: ...

    async def trigger(
        self,
        source: TriggerSource,
        *,
        episode_paths: list[Path] | None = None,
    ) -> SchedulerResult: ...
```

`episode_paths` is the optional list of session-digest markdown paths the rollup worker just produced. The scheduler hands these to the curator via the curator's existing `ingest(...)` parameter shape — read the actual signature of `WikiCurator.ingest` and adapt.

## 7. Interfaces you consume

| What | Where | How |
|------|-------|-----|
| `WikiCurator` class | `jarvis/memory/wiki/curator.py` | Construct an instance externally (in factory or in Wave-2 wiring); your scheduler only receives it via constructor injection. |
| `JarvisConfig.brain.primary` | `jarvis/core/config.py` | Not directly — the curator pulls its own brain provider. You stay out of brain config. |

## 8. Anti-patterns specific to your scope

- **Don't busy-wait inside `acquire`** — use `time.sleep(0.05)` between retries; cap total wait at `timeout_s`.
- **Don't keep the lock during the curator's LLM call without a timeout-shield.** The whole curator-run timeout is set inside the curator itself, but if it deadlocks, your lock leaks. The lock file's stale-detection handles this on next start, but you should also guarantee `release()` runs in a `try/finally` regardless of curator exceptions.
- **Don't use `fcntl` or `msvcrt` locking primitives** — they're OS-specific. Use atomic file creation (`open(path, "x")`) instead.
- **Don't store the lock file under `wiki/obsidian-vault/`** — that path is in Obsidian's watch scope and a lock file there would noise up the user's Obsidian sidebar. Use `data/` (gitignored).
- **Don't broadcast `SchedulerResult` on the bus** — the curator's existing log line is the source of truth for "did it write". Adding a second event source breaks the recall-tool's timeline reasoning. AP-6 of the overview applies.
- **Don't let `PERIODIC` trigger when `enable_periodic` is False.** A common bug pattern: the periodic timer-task still runs, then `trigger()` rejects it. Cleaner: only construct the periodic task at all when the config opts in.
- **Don't log the brain prompt or the curator output.** That's the curator's job. You log scheduler decisions only.

## 9. Pre-flight test gate

```powershell
cd C:\Users\Administrator\Desktop\jarvis-b5-agent-D
pip install -e . --no-deps
python -m pytest tests/unit/ -q --tb=no > pre-flight.log
```

## 10. Post-flight verification commands

```powershell
# 1. Unit tests for the lock
python -m pytest tests/unit/memory/wiki/test_lock.py -v
# Expected: all PASSED — including contention, stale-detection, and crash-recovery

# 2. Unit tests for the scheduler
python -m pytest tests/unit/memory/wiki/test_scheduler.py -v
# Expected: all PASSED — cooldown, lock contention, manual bypass, periodic gate

# 3. Full unit suite diff
python -m pytest tests/unit/ -q --tb=no > post-flight.log
fc /n pre-flight.log post-flight.log
# Expected: no new FAILED lines

# 4. Lock file genuinely cleaned up after a crash
python -c "from jarvis.memory.wiki.lock import VaultLock; from pathlib import Path; l = VaultLock(Path('data/test.lock'), stale_after_seconds=2); l.acquire(); print('acquired')"
Start-Sleep -Seconds 3
python -c "from jarvis.memory.wiki.lock import VaultLock; from pathlib import Path; l = VaultLock(Path('data/test.lock'), stale_after_seconds=2); print('second acquire:', l.acquire()); l.release()"
Remove-Item data/test.lock -ErrorAction SilentlyContinue
# Expected: "acquired" then "second acquire: True" (steals stale lock)
```

## 11. Closing report

Free-form Markdown, final line **must be exactly**:

```
Goal erfüllt: ja — Grund: <one sentence>
Goal erfüllt: nein — Grund: <one sentence>
```

Recommended sections: Files changed · What I did · Assumptions made · Verification (paste §10 outputs) · Open follow-ups · the mandatory line.

## 12. Worktree setup recap

```powershell
cd C:\Users\Administrator\Desktop\Personal Jarvis
git worktree add -b impl/b5-agent-D C:\Users\Administrator\Desktop\jarvis-b5-agent-D impl/b5-base
cd C:\Users\Administrator\Desktop\jarvis-b5-agent-D
pip install -e . --no-deps
```

Final commit:

```powershell
git add -A
git commit -m "feat(memory/wiki/b5/d): add CuratorScheduler + VaultLock"
```

Do **not** push.
