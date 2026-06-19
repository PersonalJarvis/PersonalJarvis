# ADR-0009 — Awareness-Layer Architecture

**Status:** Accepted (2026-04-25)
**Phase:** A0 — Foundations (the following phases A1-A5 reference this ADR)

## Context

Plan vision (see `JARVIS_AWARENESS_PLAN.md`): on the wake word, Master-Jarvis should know the thread of the last hour — which window is active, which file is open in the IDE, what the user has done in the last ~30 minutes. This context must be readable synchronously (critical-path protection: <50ms p95), but must never block the voice pipeline or trigger new brain roundtrips on the wake path.

Until now context was rebuilt anew on every Sub-Jarvis spawn (`vision_context` with UIA-tree lookup, ~200-400ms per spawn) or was missing entirely. The user phrases the missing capability like this: "When I say 'change that', Jarvis should know what 'that' is." Awareness delivers exactly this background state without costing a brain call per wake word.

## Decision

Four-layer architecture (plan §1):

| Layer | Lifetime | Component | Storage | Consumer |
|---------|-----------|-----------|---------|-------------|
| L1 Live Frame | Seconds | `AwarenessManager` + watchers | RAM (dataclass) | Master-Jarvis (sync read <50ms) |
| L2 Story | 30min rolling | `StoryTracker` + `Verdichter` (Haiku) | RAM ring buffer + SQLite | Master-/Sub-Jarvis (episode snippet in the system prompt) |
| L3 Session | 24h-7d | `SessionRecall` (FTS5) | SQLite (`jarvis.db`) | Sub-Jarvis (`awareness-recall` tool) |
| L4 Long-Term | persistent | `Curator` (existing) | `USER.md` / `MEMORY.md` | Curator is fed, not replaced |

Architecture principles (non-negotiable, plan §1):

1. **Event-driven, never polling** — Win32 event hooks (`SetWinEventHook`), file-system watchers (`watchdog`), bus subscriptions. The only permitted polling site: `IdleDetector` with a 1s tick on `GetLastInputInfo`.
2. **Critical-path protection** — `AwarenessManager` holds the L1 state in RAM. Master-Jarvis reads synchronously without a brain/IO call. Condensation (L2) happens asynchronously in the background.
3. **Privacy-first** — `PrivacyFilter` runs BEFORE every capture. Hybrid default: coding apps allow, browsers block, banking/password/incognito hard-blocked.
4. **Salience before token consumption** — `SalienceScorer` (rule-based, no LLM) filters frames before the condenser.
5. **Reuse of existing infra** — no new DB system, no parallel EventBus. `RecallStore` + `EventBus` + `Curator` are extended, not replaced.

## Rationale

**Polling vs hooks:** A 500ms polling loop on `GetForegroundWindow()` carries 0.5-2% CPU overhead permanently and swallows window switches that are faster than the polling interval. `SetWinEventHook` has <10ms latency and ~0% CPU cost. Hooks clearly win.

**FTS5 vs embeddings (D-A2):** ChromaDB/embeddings for L3 were deferred to Phase A6. FTS5 (BM25 ranking, 200ms p95 for 1000 episodes) is the simpler start. If recall metrics show FTS5 to be insufficient, a vector re-rank in A6 is additively possible — no schema-migration block.

**Direct brain call vs Sub-Jarvis spawn (D-A4):** the episode condenser runs as a direct brain call against Haiku via `BrainProviderRegistry`, NOT as `spawn_sub_jarvis`. Sub-Jarvis is for user tasks; internal condensation would trigger the JARVIS_DEPTH guard and make latency/cost explode (hard negative, plan §10).

**Hybrid privacy default (D-A1):** coding apps (`code.exe`, `cursor.exe`, etc.) are explicitly allowlisted; browsers are blocked by default because they carry high PII risk (URLs, tabs, password fields); everything else unknown is allowed (productivity tools like Notepad, Office). The user can additively block via `preferences.toml` — system defaults are hard, not removable.

## Position relative to existing plans

- **`JARVIS_REFACTOR_PLAN.md` (routing bug)**: hard-sequential — routing must be completed before A2, otherwise every episode trigger accidentally spawns a Sub-Jarvis instead of a Haiku condenser call. A0 itself does not touch any routing code.
- **`PLAN_B_CURATOR.md`**: Awareness L2 *feeds* the Curator. From Phase A2 onward, `EpisodeRecorded` is optionally routed to a second Curator subscriber (wiring in `jarvis/brain/factory.py`). Additive, no conflict.
- **Phase 5 Sub-Jarvis**: Awareness is router-tier-only for the state read. Sub-Jarvis gets `awareness-recall` (Phase A3). `AwarenessManager` is NOT passed to Sub-Jarvis spawns (Sub is stateless).
- **Phase 6 tool roster (Browser-Use, Computer-Use)**: Computer-Use actions trigger frame updates → Awareness sees these as "self-induced activity". The probe layer (A5) must distinguish between user and bot activity via `frame.source = "user" | "bot"`.

## Consequences

+ Master-Jarvis has a synchronous context read <50ms — no brain roundtrip for "What am I doing right now?".
+ Sub-Jarvis can semantically search the last 24h of activity via `awareness-recall` — without Master-Jarvis sending the whole history every time.
+ The existing Curator pipeline gets additional inputs without breakage.
+ Hot-disable: `[awareness].enabled = false` shuts down the entire subsystem (plan §15 rollback plan).
- Win32 hooks need disciplined lifecycle handling (`UnhookWinEvent`, thread joins with a 2s timeout). Memory/handle-leak risk on a faulty `stop()` implementation. Mitigation: test mandatory in A1.
- Condenser brain calls have token cost (Haiku, ~800 in / 200 out per episode, estimated <0.5 ct/episode at current pricing).
- DB bloat: frames accumulate quickly (~100/h possible). Retention via `prune_older_than(hours=24)` as a background task mandatory from A2 onward.
- Pattern break: `PrivacyFilter` is the second "system+user pattern merge" implementation in the codebase alongside `RiskTierEvaluator`. If a third filter with the same pattern emerges, a shared `PatternSource` protocol should be extracted.

## Alternatives Considered

- **Polling-only (every 500ms `GetForegroundWindow`)**: ~0.5-2% CPU permanently + higher latency + lost switches shorter than the polling interval. Discarded.
- **Own SQLite file for Awareness**: extra connection pool, separate schema migration, separate backups. Discarded — `jarvis.db` already has FTS5 and is asynchronous via `aiosqlite`.
- **ChromaDB from day 1**: ~200MB container overhead, embedding latency ~50ms per episode, vector-DB operational complexity. Discarded for A0-A5; additively extensible in A6 if FTS5 proves too weak.
- **Sub-Jarvis for condensation**: would overload the depth guard and emit additional bus events. Discarded — a direct Haiku brain call via `BrainProviderRegistry` is more efficient.
- **Extend `vision_context` (UIA-tree lookup) instead of an Awareness subsystem**: would make the 200-400ms spawn-latency cost permanent. The Awareness RAM state is a <50ms read and persists across spawns. Discarded.

## Update: Phase A2 — L2 condensation procedure (2026-04-26)

A2 has landed. Concrete implementation:

**Slices (3-teammate agent team, parallel):**
1. **Persistence (Slice A):** `awareness_frames` + `awareness_episodes` + `awareness_episodes_fts` (FTS5 with AFTER-INSERT trigger) in `schema.sql`. `RecallStore.record_frame/record_episode/recent_episodes/search_episodes` async via aiosqlite. 7 tests green.
2. **Data structure + salience (Slice B):** `Episode` (frozen+slots) + `EpisodeBuilder` (mutable) in `episode.py`. `SalienceScorer` with `SALIENCE_THRESHOLD=30`, `BORING_PROCESSES` case-insensitive (Explorer.exe etc.), additive components +20/+30/+20/+10/-50, clamped [0, 100]. 34 tests green.
3. **Condenser (Slice C):** direct brain call via `BrainProviderRegistry.instantiate(provider, model)` + `Brain.complete(BrainRequest)` + `streaming.aggregate(stream)`. `asyncio.wait_for(timeout_s=5.0)`. Hard cap 30 frames+events (chronological tail strategy). Hard negative §6 multi-layer enforced via test: source audit (tokenize-stripping), signature check, behavior spy. 9 tests green.

**Lead synthesis (`StoryTracker` in `story.py`):**
- Bus subscriber on `FrameUpdated`, `IdleEntered`, `ResponseGenerated` (proxy for `BrainTurnCompleted`)
- The trigger order in `_on_frame_updated` is CRITICAL: **app-switch detection FIRST, salience filter SECOND**. The score of an app-switch frame is only +20 < SALIENCE_THRESHOLD, so it would otherwise be dropped before the flush check. Plan §6 requires app-switch as a trigger independent of new-frame salience.
- Min-duration skip (60s default) prevents spam episodes during rapid tab switching
- Hard timer (5min default) flushes silent sessions
- Buffer overflow (200 frames default) forces a flush regardless of duration
- Lock pattern: a single `asyncio.Lock` protects `_builder` + `_prev_frame` against re-entry during the asynchronous condenser call

**Condenser prompt (tournament 2026-04-26):** 3 variants evaluated against each other by code-reviewer. Pick: **Variant 3 (few-shot with negative examples)** — score 9/9/9/8 vs. plain-plan 3/2/2/10 and strict-whitelist 8/8/8/7. Rationale: Haiku 4.5 demonstrably responds better to identical input with a correct-vs-hallucinated example + rationale than to declarative verb blacklists. The plain-plan example "arbeitet seit 23min an X" conditions the model precisely on the hallucination pattern. The V4 iteration (stuck-pattern + PII whitelist) is a follow-up after the live test.

**Defense-in-depth against hard negatives:**
- HN1 (no spawn_sub_jarvis in the condenser): source audit with `tokenize`-stripping (not a naive `in src`), signature check (`brain` + `config`, no `manager`), behavior spy (1 brain.complete call, request.tools==()).
- HN2 (PrivacyFilter-blocked frames not in the input): filter in `_on_frame_updated` BEFORE the frame goes into the builder. An E2E test with a mix of allowed/blocked verifies that blocked titles never land in the condenser call.
- HN3 (asyncio.wait_for timeout): the condenser wraps brain.complete in `wait_for(timeout=5.0)`, returns a graceful empty + error_reason="timeout" on overrun.
- HN4 (max 30 frames+events): Verdichter._cap_to_max sorts by ts, keeps the newest 30, drops older ones.

**Tests (as of 2026-04-26):**
- Slice A: 7 (test_episode_persistence)
- Slice B: 34 (test_episode + test_salience)
- Slice C: 9 (test_verdichter)
- Synthesis: 19 (test_story_tracker) + 5 (test_a2_e2e)
- Total Phase A2: 74 new tests, all green

**Follow-up ADRs:**
- ADR-0010 was assigned in Phase A1 for `WindowFocusWatcher` + MsgWaitForMultipleObjects (NOT for Awareness vs. Curator as originally planned here). The Awareness-vs-Curator question is answered by plan §6 + this ADR update: the condenser is an Awareness-internal component, the Curator is fed unchanged from `EpisodeRecorded` as an optional subscriber (in A4 or later).
- ADR-0011 was assigned in the persona refactor for router discipline (NOT for privacy-hybrid as originally planned). Privacy-hybrid is sufficiently documented via plan §4 + the existing `PrivacyFilter` implementation.

## Update: Phase A5-Lite — Deep Probes (Git + FileSystem) (2026-04-26)

A5-Lite has landed. Plan §9 has 4 probes (Git/FileSystem/MCP/LSP) and is explicitly marked as "optional, can be deferred to Phase 6". We implement only the two simple ones — MCP+LSP follow as a separate phase.

**Slices (parallel via agent team):**
1. **GitProbe** (`jarvis/awareness/probes/git.py`): `.git/HEAD` direct-read as PRIMARY (50ms timeout) + `asyncio.create_subprocess_exec("git", "rev-parse", "--abbrev-ref", "HEAD")` as FALLBACK (150ms timeout). Worktree repos are resolved correctly via `gitdir:` parsing. Detached HEAD returns an 8-char SHA prefix. NO sync subprocess — all asyncio. 9 tests green.

2. **FileSystemProbe** (`jarvis/awareness/probes/filesystem.py`): watchdog observer scoped to project roots (`recursive=True` but only for the root, NEVER for the system root). Cap `_MAX_WATCHED_ROOTS=10`. Debounce 200ms against editor atomic-save bursts. Blacklist (`.git`, `__pycache__`, `node_modules`, `.venv`, …) against build-tool noise. Bus publish via `loop.call_soon_threadsafe` (NEVER directly in the watchdog thread). New bus event `FileSaved(path, process_name, repo_root)`. 9 tests green.

**Lead synthesis:**
- `probes/base.py`: Probe protocol (`runtime_checkable`)
- `manager.probe_all(pid, process_name)`: psutil-cwd-resolve + asyncio.gather with `return_exceptions=True` + `asyncio.wait_for(timeout=config.probes.total_budget_ms/1000)`. Probe errors become missing fields, no crash.
- `watchers/window.py:_drain_once`: calls `manager.probe_all(pid=pid, ...)` ONLY for allowed frames (privacy + cost protection). Defensive try/except — if probe_all crashes, FrameUpdated is still emitted (with git_branch=None, open_file_hint=None).
- `manager.start()`: starts `_fs_probe` (watchdog observer) BEFORE StoryTracker
- `manager.stop()`: stops `_fs_probe` AFTER StoryTracker, BEFORE watchers
- `factory.py`: registers probes analogously to the Verdichter/StoryTracker pattern
- `config.AwarenessProbesConfig`: `enabled`, `enable_git`, `enable_filesystem`, `total_budget_ms=200`, `fs_max_watched_roots=10`
- `jarvis.toml`: `[awareness.probes]` block

**Hard negatives §9 (all enforced + tested):**
- HN1: NO sync subprocess (`asyncio.create_subprocess_exec`) — code audit clean
- HN2: FileSystemWatcher scoped, NO C:\ — `_MAX_WATCHED_ROOTS=10` cap + per-root scheduling
- HN3: probe errors NEVER propagate — try/except per probe + return_exceptions=True on gather
- HN4: 200ms hard total budget — `asyncio.wait_for` enforced

**Tests (as of 2026-04-26):**
- Slice A: 9 (test_git_probe)
- Slice B: 9 (test_filesystem_probe)
- e2e synthesis: 6 (test_a5_e2e — empty/merge/timeout/exception/real-git/lifecycle)
- Total Phase A5: 24 new tests + 0 regression on the existing 152 awareness tests

**Pattern model for A6+ (probe extension):** the Probe protocol allows a drop-in MCP/LSP/any-other probe without changing the manager or a watcher. Only extend `factory.py` + implement the probe class.

## Update: Phase A2 Codex BLOCKER B1+B2 Resolution (2026-05-11)

The Codex adversarial review of the A2 story layer identified two race
conditions (see `JARVIS_AWARENESS_PLAN.md` Phase-A2 Codex review).
Both are now **resolved** with the snapshot-then-dispatch pattern in
`StoryTracker` plus the double-buffer pattern in `EpisodeBuilder`.

**B1 — lock holding across the condenser call (bug)**

Previously `_flush_to_verdichter` held the `asyncio.Lock` across the ~5s
condenser RPC. Every further `FrameUpdated` / `IdleEntered` /
`ResponseGenerated` handler blocked 5s on `async with self._lock`.
With 50 parallel frame updates the throughput would rise from ~0ms to
several seconds.

Fix: separation of snapshot (sync, inside the lock) and dispatch (async,
lock-free):

- `_extract_snapshot_locked(trigger_kind=...) -> _FlushSnapshot | None`
  is SYNC. The caller MUST hold `self._lock`. It validates the builder
  against the min-duration spam protection, extracts frames+events via
  `EpisodeBuilder.detach_*` (double buffer), computes `primary_app`
  via `primary_app_from_snapshot`, and sets `self._builder = None`. No
  `await`, no yield point — atomic against every other bus handler.
- `_run_flush(snap) -> None` is ASYNC. The caller MUST have released
  `self._lock`. It calls the condenser, persists into RecallStore,
  updates `manager.state` and publishes `EpisodeRecorded`.

Every bus handler follows the same scheme:

```python
pending: list[_FlushSnapshot] = []
async with self._lock:
    snap = self._extract_snapshot_locked(trigger_kind="...")
    if snap is not None:
        pending.append(snap)
for snap in pending:
    await self._run_flush(snap)
```

`_on_frame_updated` may extract up to two snapshots in one lock
acquisition (app-switch + buffer/max-duration overflow), both run
sequentially outside the lock. The convenience method `_maybe_flush`
(used by the hard timer + tests) also acquires the lock itself and
releases it again before `_run_flush` — callers of `_maybe_flush` must
NOT hold the lock (otherwise a deadlock on the asyncio.Lock, which is
not reentrant).

**B2 — frame loss between snapshot and buffer reset (bug)**

Previously `EpisodeBuilder.frames` returned a list-comprehension copy
but left the internal `_frames` buffer standing — the reset to
`self._builder = None` lived in the `StoryTracker`. With the
B1 snapshot-then-dispatch pattern, a race window opens between snapshot
creation and `self._builder = None`, in which a parallel `add_frame`
could land in the doomed buffer.

Fix: double-buffer pattern directly in `EpisodeBuilder`:

- `detach_frames() -> list[FrameSnapshot]` extracts the frame list
  and replaces the internal `_frames` with a fresh empty list
  BEFORE it returns. A single sync block, no await — under
  asyncio single-thread + an external lock the operation is atomic.
- `detach_events() -> list[dict]` identical for events.
- `primary_app_from_snapshot(frames)` as a module-top helper computes
  the primary_app on detached frames (the builder is empty after detach).

The property getters `frames`/`events` remain as read-only copies —
they are still the preferred path for inspection
(tests, live-state lookup). Only the flush path uses `detach_*`.

**Test coverage**

- `tests/unit/awareness/test_story_tracker.py::test_b1_lock_free_during_verdichter_call`
  — AC-strict: the condenser sleeps 5s, 50 parallel `_on_frame_updated`
  in <0.1s total. Without the B1 fix this would be 5s+ per push.
- `tests/unit/awareness/test_story_tracker.py::test_b1_idle_handler_does_not_block_frame_handler`
  — an idle flush with a 0.4s condenser sleep does not block a parallel
  frame update (<0.1s).
- `tests/unit/awareness/test_story_tracker.py::test_b2_no_frame_loss_during_concurrent_flush`
  — a frame during flush lands in the new builder (with FakeRecall).
- `tests/unit/awareness/test_story_tracker.py::test_b2_extract_snapshot_locked_resets_builder`
  — `_builder` is `None` after `_extract_snapshot_locked`, the snapshot
  contains all frames.
- `tests/unit/awareness/test_episode_persistence.py::test_frame_during_flush_lands_in_next_episode_buffer`
  — end-to-end with a real `RecallStore`: a frame during flush lands
  in episode 2 (persisted via `stop()`), not in episode 1, no
  frame lost.

51 tests in the B1/B2 scope (`test_story_tracker.py` + `test_episode.py`
+ `test_episode_persistence.py`) all green.

**Codex review status:** B1 and B2 **resolved**.

## Update: Phase A4 — Working Set Multi-Context LRU (2026-05-11)

**Problem (plan §8):** before A4 the ``AwarenessState`` only knew the
most recently flushed episode (``last_episode_summary``). When the user
switched workflow between several parallel contexts (e.g. VS Code →
Slack → VS Code), ``snapshot_for_prompt`` always showed the "last
whatever" episode. When the user switches back to VS Code via wake word,
they landed prompt-side in the Slack thread — wrong awareness.

**Solution:** ``WorkingSet`` as a per-manager LRU cache of ``Context``
drawers. Each ``Context`` is (project_root, task_label,
last_episode_id, last_seen_ns). On re-activation of a known
project_root the corresponding slot is promoted (not a new one
created). Cap = 5 slots (plan §8).

**Architecture**

- ``jarvis/awareness/context.py`` — ``Context`` frozen dataclass +
  ``resolve_context(frame) -> Context`` heuristic:
  1. IDE_SET (code.exe / cursor.exe / windsurf.exe / …) → ``project_root``
     via ``psutil.Process.cwd()`` (lazy import, fail-silent).
  2. BROWSER_SET (chrome.exe / firefox.exe / …) → hostname from the
     window title (URL match then domain match, browser suffix stripped).
  3. TERMINAL_SET (WindowsTerminal.exe / pwsh.exe / …) → cwd via psutil.
  4. Fallback → ``process_name`` as the identity key (always valid).
  ``task_label`` = the first 5 words of the window title.
- ``jarvis/awareness/working_set.py`` — ``WorkingSet`` LRU cache with
  ``OrderedDict`` backing. ``observe(ctx)`` does insert or promote;
  ``set_episode(root, id)`` links a persisted episode ID with
  the slot without an LRU promote. ``render_for_prompt()`` renders a
  multi-context block for system-prompt injection (empty with <= 1 slot).
- ``jarvis/awareness/manager.py`` — ``AwarenessManager`` holds one
  ``WorkingSet`` per instance, additionally subscribes in ``start()`` to
  ``FrameUpdated`` (→ ``observe``) and ``EpisodeRecorded`` (→
  ``set_episode``). On app-switch a ``ContextSwitched`` event is
  published (UI/flight recorder).
- ``jarvis/awareness/state.py`` — ``AwarenessState.working_set`` is now
  ``WorkingSet | None`` (previously ``list[Any]``); ``snapshot_for_prompt``
  calls ``working_set.render_for_prompt()`` between the idle status and
  the last-episode line.

**Hard negatives (all observed)**

- ❌ ``WorkingSet`` is NOT persisted — RAM-only. SQLite has all
  episodes via ``RecallStore``.
- ❌ No singletons — one per ``AwarenessManager`` (DI).
- ❌ Eviction does NOT delete episodes — only the RAM pointer; episodes
  stay unchanged in ``awareness_episodes``.
- ❌ ``psutil`` import NEVER at the module top level — lazy import in
  ``_safe_cwd`` with ``try/except`` on all operations (CI/Linux-flaky).

**Acceptance criteria (all met)**

- A→B→A: ``WorkingSet.current`` shows A again (re-promotion). Test:
  ``test_promote_a_then_b_then_a``, ``test_a_then_b_then_a_yields_distinct_then_resumed_contexts``.
- ``WorkingSet.size <= 5`` (LRU evicts the oldest). Test:
  ``test_observe_seven_contexts_with_max_five_evicts_oldest_two``.
- Eviction triggers no episode deletion — verified via
  ``test_eviction_does_not_affect_episode_id_field`` (in RAM the
  ID stays visible in the evicted object; the DB is unaffected because there is no DB call).
- No API change to the ``AwarenessManager`` public surface — backward-compat
  to A0/A1 preserved (``AwarenessState.working_set`` default = None).

**Test coverage**

- ``tests/unit/awareness/test_working_set.py`` — 16 tests: LRU eviction,
  re-promotion, set_episode linkage, render format, max_slots validation.
- ``tests/unit/awareness/test_context_resolution.py`` — 24 tests:
  IDE/browser/terminal/fallback heuristic, hostname parser,
  task_label truncation, psutil lazy import + fail-silent.

40 new tests green. The full suite ``tests/unit/awareness/`` +
``tests/integration/awareness/`` = 239 passed.

## References

- **Plan:** `JARVIS_AWARENESS_PLAN.md` §1-§16 (binding spec)
- **A1 Win32 lifecycle:** ADR-0010 (`WindowFocusWatcher` with MsgWaitForMultipleObjects instead of PumpMessages)
- **Pattern models:** `jarvis/safety/risk_tier.py` (pattern merge), `jarvis/vision/screenshot.py` (lazy Win32 imports), `jarvis/core/events.py` (frozen events with trace_id), `jarvis/memory/curator/__init__.py` (separate brain instance via BrainProviderRegistry — A2 condenser model)
