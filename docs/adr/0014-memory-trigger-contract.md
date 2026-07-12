# ADR-0014 — Memory-Trigger Contract (silent vs loud failure discipline)

**Status:** Accepted · **Date:** 2026-05-14 · **Phase:** B8 (Memory Hardening)

> Note: this ADR shares the 0014 prefix with
> `0014-flash-brain-suppress-if-fast.md` (Voice UX hardening). The
> repository already tolerates duplicate ADR numbers across unrelated
> sub-systems (see 0009 awareness / 0009 self-healing,
> 0010 output-filter / 0010 window-focus-watcher). Each ADR remains
> independently addressable by full filename.

## Context

Three independent triggers feed the wiki memory subsystem:

1. **`WikiContextInjector`** — runs before each brain turn
   (`jarvis/brain/wiki_context.py`); injects matching vault snippets
   into the system prompt.
2. **`VoiceFactBridge`** — subscribes to `TranscriptFinal` and
   `ResponseGenerated` (`jarvis/memory/wiki/voice_bridge.py`); routes
   user-spoken facts to the curator via the ack path (B5) or the
   aggressive path (B8).
3. **`SessionRollupWorker`** — subscribes to `IdleEntered`
   (`jarvis/memory/wiki/session_rollup.py`); rolls awareness episodes
   into one Markdown digest per work session.

The 2026-05-14 incident exposed three failure-discipline gaps that
this ADR closes.

**Silent fallback masked a real outage for 12+ hours.** The rollup
worker had been crashing on every `IdleEntered` since 2026-05-13 21:38
with `GeminiBrain.complete() got an unexpected keyword argument
'max_tokens'`. The worker handled the exception, logged
`wiki_integration: rollup status=llm_failure ...` at INFO level, and
returned cleanly. The user's mid-term memory stayed empty across three
work sessions without any visible signal — no warning, no telemetry
counter, no UI indication. The recovery only happened because an
operator grepped a stack trace for an unrelated reason.

**Working-tree drift was inert until it bit.** Five tracked files
under `jarvis/memory/wiki/` (`integration.py`, `lock.py`,
`scheduler.py`, `search.py`, `voice_bridge.py`) disappeared from the
working tree while still present in HEAD. The running app loaded
stale `.pyc` caches and the memory pipeline ran at half effectiveness
for hours. There was no pre-boot check to surface the drift; the
recovery script (`scripts/check-working-tree.ps1`) only landed as
part of B8.4.

**The ingest path was narrower than the brain prompt promised.** The
ack-keyword filter ("notiert", "vermerkt", ...) only fires when the
brain explicitly acknowledges storage. A fact like "Ich war heute mit
Carlos Pad-Thai essen" + brain reply "Klingt lecker, war es scharf?"
silently fell through every ingest path. B1 §3.8 had foreseen a
second, salience-LLM-gated aggressive path but never activated it.

The pattern across all three: **failure modes were inadequately
classified.** "Silent and graceful" is sometimes correct (the voice
path must never crash because the wiki write failed). "Loud and
counted" is sometimes mandatory (a permanent trigger outage must be
operator-visible within minutes, not hours). The codebase did not
distinguish, so by default everything was silent.

## Decision

Three explicit contracts for the memory subsystem. Any new memory
trigger introduced after this ADR MUST comply.

### 1. Trigger inventory is binding

Every memory trigger is listed here, with the event it subscribes to,
the side effect it produces, and the failure-loudness class it falls
into (per contract 2 below). The table is the single source of truth;
removing or renaming a trigger requires amending this ADR.

| Trigger | Subscribes to | Side effect | Failure class |
|---|---|---|---|
| `WikiContextInjector` | direct call before each brain turn | prepends `## Wiki context` to system prompt | **silent fallback** — missing context degrades gracefully to unaltered prompt |
| `VoiceFactBridge` (ack path) | `ResponseGenerated` with ack keyword | calls `WikiCurator.ingest()` in background | **loud regression** — every ingest failure increments `voice_turns_ingested_ack` discrepancy and is exception-logged |
| `VoiceFactBridge` (aggressive path) | `ResponseGenerated` without ack, > 30 chars | calls `WikiCurator.ingest()` in background, rate-limited | **loud regression** — same as ack path, plus rate-limit logged at DEBUG |
| `SessionRollupWorker` | `IdleEntered` past `session_idle_threshold_minutes` | writes one session Markdown page | **loud regression** — failure increments `session_rollups_failed` and is WARNING-logged |

New triggers (planned in B6: external-source ingest, Jarvis-Agents
context hints) MUST be added to this table in the same PR that adds
their code.

### 2. Failure-loudness is classified per trigger

Every trigger declares exactly one of two failure classes. The class
governs how a failure surfaces.

**Silent fallback.** Used when:

- The trigger's purpose is *enhancement*, not *persistence*.
- A failure is operationally indistinguishable from "nothing to do".
- The voice path or brain turn must continue unblocked regardless.

Allowed behaviour on failure: log at DEBUG/INFO, return a benign
default (empty list, unchanged prompt, no-op). No telemetry counter
is required.

Example: `WikiContextInjector` returns the unchanged system prompt on
search timeout, no-keywords, or zero hits. The user sees the same
brain response either way; the only difference is whether the brain
had wiki context to lean on.

**Loud regression.** Used when:

- A failure means user data was lost or never persisted.
- A failure means the subsystem is structurally broken (not
  transient).
- An operator needs to know within minutes, not days.

Required behaviour on failure: log at WARNING (or higher),
increment a named counter in `jarvis.memory.wiki.telemetry`, and
remain alive so the next event has a chance to succeed. Raising
exceptions across the bus boundary is forbidden — the voice path
must never crash because a memory trigger failed — but the failure
MUST leave a grep-able audit trail.

Example: `SessionRollupWorker.flush_session()` returning
`status="llm_failure"` MUST also call
`telemetry.inc("session_rollups_failed")`. The hourly summary line
(B8.7) then surfaces a non-zero counter to operators within the
hour, not after a 12-hour grep session.

The two classes are mutually exclusive. A trigger cannot be silent
on some failures and loud on others — that ambiguity is what masked
the 2026-05-13 outage.

### 3. Aggressive-mode safety contract

Any second-path ingest mode that fires without an explicit
acknowledgement signal (today: `VoiceFactBridge` aggressive path;
future: Jarvis-Agents context hints) MUST satisfy three structural
properties.

**Configurable cost gate.** A per-source minimum interval is available via
the trigger's config block (today: `[memory.wiki.voice_bridge]
.rate_limit_seconds`, default 0). Zero reviews every eligible completed turn;
a positive value is an explicit operator choice that trades memory completeness
for fewer provider calls. The limiter uses `time.monotonic_ns()` so wall-clock
drift cannot lift a configured gate spuriously.

**Opt-out via config.** The mode is governed by a boolean
(`aggressive_mode`, default `true`). Setting it to `false` MUST
disable the aggressive path entirely while leaving the explicit
ack path intact. This is the kill-switch for "the LLM is too
chatty, please stop curating my smalltalk".

**Asynchronous.** The handler that decides to fire MUST spawn the
actual ingest as `asyncio.create_task(...)` and return immediately.
The bus dispatcher and the voice critical path must never await
a curator call directly. Failure of the spawned task MUST be
caught with `try/except Exception` inside the task body (already
the contract in `VoiceFactBridge._ingest_safe`) and routed
through the loud-regression failure class.

These properties keep the reviewer controllable (cost gate + opt-out) and off
the voice critical path (async) without silently dropping consecutive durable
facts on a default installation.

## Consequences

**Prevented by this contract**

- Silent outages of *persistence* triggers cannot last beyond one
  hourly summary line.
- Adding a new memory trigger without classifying its failure mode
  becomes a documentation failure (this ADR is the gate).
- An aggressive-ingest mode that bypasses rate-limiting or
  asynchronous spawning fails review against this ADR.
- A user who finds the aggressive mode too eager has a one-line
  opt-out (`aggressive_mode = false` in `jarvis.toml`).

**New work required**

- Every existing trigger has been re-audited against the contract;
  the inventory table is current as of 2026-05-14.
- Future PRs that touch `jarvis/memory/wiki/` MUST update the
  inventory table in the same change if they add, remove, or
  rename a trigger.
- The hourly summary loop (B8.7) is now load-bearing for the
  loud-regression class — its disablement requires a successor
  observation mechanism.
- The recovery banner from `scripts/check-working-tree.ps1` (B8.4)
  is now a contractual prerequisite for catching working-tree
  drift before it manifests as half-loaded memory code.

## Alternatives Considered

**Make every failure loud.** Rejected. A flaky vault-search hit
(`WikiContextInjector`) is operationally indistinguishable from
"this turn had no useful context". Counting every miss as a WARNING
would generate noise that drowns the real regressions and would
encourage the next operator to disable the noisy logger entirely.
The injection path's purpose is enhancement, not persistence — the
appropriate failure mode is silent.

**Make every failure silent (status quo).** Rejected. The
2026-05-14 incident is the exact failure mode this option produces:
a persistence path stops working, no observable signal fires, the
operator notices only when downstream features start lying. The
voice-fact ingest path is persistence-critical, not enhancement.

**Auto-disable triggers on N consecutive failures.** Rejected.
Self-disabling memory triggers introduce a second, hidden state
(`disabled-due-to-failures`) that the operator has to learn about
to debug. The contract chosen here keeps every trigger alive across
failures and surfaces the failures explicitly, leaving the decision
to disable to humans editing `jarvis.toml`.

**Move the aggressive-mode gate into the brain prompt instead of
config.** Rejected. The brain prompt is text and can be overridden
by adversarial inputs ("ignore your salience rules"). The config
toggle is enforced in Python (the bridge constructor reads it once
at startup and the dispatcher checks `self._cfg.aggressive_mode`
before every fire); prompt drift cannot bypass it. Salience inside
the curator prompt (B8.9) and the rate-limit in the bridge are
complementary defences, not substitutes.

## References

- [`0013-knowledge-wiki-architecture.md`](0013-knowledge-wiki-architecture.md)
  — long-term memory tier this contract governs. Establishes the
  three-tier memory hierarchy and the curator-via-prompt pattern.
- [`0009-self-healing-worker-critic.md`](0009-self-healing-worker-critic.md)
  — the action/observation invariant. Same failure-discipline shape:
  some failure modes must remain loud (Critic verdict failure) and
  some must remain silent (sycophancy filter no-op). Cross-reads as
  the design ancestor for this ADR.
- [`0009-awareness-architecture.md`](0009-awareness-architecture.md)
  — the awareness pipeline that produces the `awareness_episodes`
  table the rollup worker reads. Idle-detection contract lives here.
- B8 plan, `docs/plans/...` and inline tasks B8.1–B8.13. This ADR is
  numbered B8.8 in that plan and lands alongside the test suite in
  B8.10 (`tests/integration/memory/test_three_triggers.py`).
- `jarvis/memory/wiki/telemetry.py` — the counter store this ADR
  relies on for the loud-regression class.
- `scripts/check-working-tree.ps1` — the working-tree drift guard
  that prevents the second-failure mode covered in Context.
