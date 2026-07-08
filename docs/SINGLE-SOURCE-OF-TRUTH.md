# Personal Jarvis — Single Source of Truth

> **Purpose.** One self-contained, authoritative narrative of the entire Personal Jarvis
> project — vision, philosophy, architecture, subsystems, and the hard-won engineering
> lessons. Written to be read end-to-end (by a human or by an AI generating a summary,
> presentation, or explainer). Synthesized from `CLAUDE.md`, the cloud-first charter, the
> architecture contracts, and the bug register.
>
> **Status:** Phases 0–7 live, plus Awareness (A0–A5), Knowledge Wiki, Jarvis-Agents bridge
> (waves 1+4), Ack-Brain, and the CLI catalog. Cross-platform (Linux/macOS/Windows)
> migration in progress.

---

## 1. What Personal Jarvis Is

Personal Jarvis is a **voice-driven meta-orchestrator**. It is *not* a classical voice
assistant that answers questions from a fixed skill list. The core pattern is a
**Supervisor–Agent architecture**: a lightweight, fast "Talker" brain holds the
conversation, and the moment real work appears, it dispatches that work to interchangeable
**harnesses** — a Jarvis-Agents subprocess, a Codex CLI, Open Interpreter, a Python script, or
a remote MCP server — which execute in the background.

The voice layer is just the interface. The intelligence is in the orchestration: deciding
*what* needs doing, *who* should do it, *whether the result is correct*, and *how to
recover* when it is not — all without breaking the flow of a single spoken conversation.

**The defining UX contract:** one uninterrupted spoken conversation. The user talks; Jarvis
acknowledges instantly ("Geht klar"); work happens off the transcript; results are spoken
back at the next natural turn boundary. The user never waits on a spinner.

---

## 2. The Cloud-First Doctrine (the most important rule)

Every architectural decision is evaluated against **one hypothetical user**: someone on a
fresh `python:3.11-slim` Linux container — 1 vCPU, 1 GB RAM, no GPU, no audio hardware, no
Windows APIs, only a network connection and a browser.

**Rule #1 — cross-platform is non-negotiable.** Everything must run on Linux, macOS, *and*
Windows, plus a headless Linux server. A feature that works on only one OS is **incomplete**,
not "done with a known limitation." The base `pip install` and boot must succeed on a fresh
Linux container, on macOS, and on Windows.

Consequences that shape the whole codebase:

- **All five provider classes (Brain, STT, TTS, Vision, Wake) have a fully cloud-reachable
  default path.** No required local GPU, no required local model, no required Windows API,
  no required microphone, no required speaker.
- **Compute-device selection is CPU-first (ADR-0024).** The default device is always
  `cpu`; a GPU is used only when a component is *explicitly* asked for one via config
  **and** a capability verdict confirms it is usable. One central policy —
  `jarvis/core/device.py::resolve_device` — expresses this: `auto`/empty/unknown resolve
  to CPU, an explicit `device = "cuda"` is the honored opt-in, and a known-bad GPU
  degrades to CPU with a logged warning. The capability verdict is *injected*, so the
  always-on wake path keeps its strict out-of-process inference gate (AP-25) while the
  latency-tolerant utterance path relies on the backend's self-heal.
- **The headless VPS + browser UI is a first-class runtime.** A user opening the
  FastAPI/WebSocket frontend in any browser — using the browser's mic and speakers, or a
  channel adapter (Telegram, Discord, SMS, webhook) — reaches the full
  Router-Brain → Worker-Critic → Mission-Manager experience without installing anything
  native.
- **OS-specific code is allowed only behind three gates:** a runtime capability check, an
  extras group (`[desktop]`, `[desktop-macos]`), and a graceful no-op (with a clear English
  message) on platforms that lack the capability.
- **The maintainer's RTX 5070 Ti / Windows 11 workstation is a power-user profile, not a
  baseline** — it represents <0.1% of the intended install base. Tray app, Orb overlay,
  global-hotkey wake, local Whisper, in-process VAD, Computer-Use harness: all opt-in
  extras that degrade gracefully.

The decision lens for any change: *Would this work, end-to-end, for the VPS user?* If yes,
ship it. If no, either gate the local-only part behind an extras group with a graceful
fallback, or split the work.

---

## 3. The 8-Layer Architecture

```
L7 UI/UX           Tray, Toasts, Admin-API, Desktop-App (FastAPI+React+pywebview), Orb-Overlay
L6 Orchestrator    State-Machine, Router, BrainManager, Supervisor, Mission-Manager
L5 Harness-Adapter OpenClaw, Codex, Open Interpreter, Python-Script, MCP-Remote
L4 Brain           5 providers (Claude-API, OpenRouter, OpenAI, Gemini, Grok) + Ack-Brain sub-second tier
L3 Intent/Risk     Classifier, Risk-Tier-Policy, Approval, Rate-Limit-Tracker
L2 Speech          Wake -> VAD (Silero) -> STT (faster-whisper / Google) -> TTS (Gemini Flash / Grok-Voice / SAPI5)
L1 Audio I/O       WASAPI via sounddevice, Device-Routing, Chime-Feedback
L0 OS/Hardware     Win32, CUDA, Mic/Speakers, global-hotkeys
```

**The strict dependency rule.** Higher layers reach lower layers **only through protocols**
(`jarvis/core/protocols.py`). Lateral communication happens **only** via typed events on the
`EventBus` (`jarvis/core/bus.py`). This keeps the layers swappable and testable in isolation,
and it is what lets the same orchestration core run on a Windows desktop or a headless VPS —
only the bottom layers (L0–L2) change.

### The Event Bus

- Events are `frozen=True` dataclasses carrying a `trace_id` (UUID) and `timestamp_ns`.
  Immutability enables flight-recorder replay: every event can be re-played to reconstruct
  exactly what happened.
- `subscribe_all` receives every event — the flight recorder is a wildcard subscriber.
- **A broken subscriber is logged and swallowed, never propagated.** One faulty handler must
  never kill the pipeline (`_safe_dispatch`).

### Streaming first

All `Brain`, `STT`, `TTS`, and `Harness` provider methods return `AsyncIterator[...]`.
Non-streaming providers yield exactly one element. Consumers always write
`async for chunk in provider.xxx()`. This means latency-sensitive paths can start speaking
the first sentence while the rest is still being generated.

---

## 4. The Plugin System

Plugins live under `jarvis/plugins/<group>/<name>.py` and register via `pyproject.toml`
entry-points (`[project.entry-points."jarvis.<group>"]`). The system is **structural, not
nominal**: a plugin must satisfy the *shape* of a protocol, but **must not import from
`jarvis.*`** inside the plugin module. This keeps plugins decoupled and independently
distributable.

The seven frozen plugin groups: `jarvis.wakeword`, `jarvis.stt`, `jarvis.tts`,
`jarvis.brain`, `jarvis.harness`, `jarvis.tool`, `jarvis.channel`.

After editing entry-points, you must re-run `pip install -e . --no-deps` to make the new
plugin active — a subtle trap that has caused real "my fix doesn't work" incidents.

---

## 5. The Brain Layer + Ack-Brain

**Multi-provider is mandatory — never hardcode Anthropic/Claude.** The maintainer has no
Anthropic API account; the primary brain is configured via `cfg.brain.primary` (currently
Gemini), with a smart fallback chain in `BrainManager`. Five providers are wired:
Claude-API, OpenRouter, OpenAI, Gemini, Grok. Runtime switching by voice ("Jarvis, switch to
Gemini") is a plan requirement.

Background **workers** use the `claude-cli` backend via Claude Max OAuth (Sonnet) — this is
the one place Claude is used, and it goes through OAuth, not an API key.

**Ack-Brain (the pre-thinking tier).** A separate sub-second model (Gemini 3.1 Flash Lite,
Grok fallback) emits a short, butler-style acknowledgement *before* the deep brain has
finished thinking. A "suppress-if-fast" gate at 2000 ms keeps the ack out of the way when
the deep brain is already quick. This is what makes the conversation feel instant.

---

## 6. The Risk-Tier Safety System

Every tool action is classified into one of four tiers: **`safe` / `monitor` / `ask` /
`block`**. The resolution priority is **blacklist > whitelist > tool default**.

The whitelist is the **anti-confirmation-fatigue contract**: a whitelisted pattern downgrades
its tier to `safe` (marked `approved_by="whitelist"`), so the user is not nagged for routine,
known-safe actions. This is a deliberate UX value — confirmation fatigue trains users to
click "yes" blindly, which is *less* safe.

**A direct call to `Tool.execute()` is a bug.** Only `ToolExecutor.execute()` is authorized —
it is the single chokepoint that enforces the risk tier, the whitelist, and plausibility
checks. Bypassing it skips all three.

---

## 7. Router Discipline (a pure dispatcher)

The router-tier brain is a **pure dispatcher**, not an actor. Its entire tool surface is the
`ROUTER_TOOLS` frozenset. Anything outside that set is delegated to Jarvis-Agents via
`spawn_openclaw`.

A force-spawn heuristic decides when to dispatch heavy work:

- **Smalltalk allowlist wins** → never spawn (don't spin up a worker to say hello).
- **Action verb** (read/build/install/open/do/show + repair words) → spawn.
- **External-system marker** (PR / repo / GitHub / issue) → spawn.

**The Jarvis-Agent tier was deleted (Welle 4).** Only `"router"` remains. Re-introducing a
`SUB_TOOLS` set, or putting any spawn tool (`spawn-openclaw`, `dispatch-with-review`,
`run-skill`) into a *worker* tool set, breaks the recursion guard — a worker would be able to
spawn its own supervisor, causing an infinite loop. This is anti-pattern AP-5/AP-14.

---

## 8. The Voice Output Filter

Brain output destined for TTS passes through `scrub_for_voice` — **regex only, no LLM
calls** (a hard latency mandate; an LLM call here would tank time-to-first-audio). It strips
tool-call leaks, jargon, markdown, self-reference, and filler.

A small **whitelist is sacred and never scrubbed**: Datei, Email, Browser, Terminal, Notiz,
Termin, Kalender. Hyphenated compounds are preserved (`Browser-Provider` stays intact).

Two TTS paths are wired through the scrubber: the main utterance path
(`_handle_utterance → _speak → tts.synthesize`) and the announcement path
(`_on_announcement` — skill/Jarvis-Agent announcements and the Jarvis-Agents `summary_de` readback).

---

## 9. Optimistic Execution & the "Oops" Protocol

This is the contract that makes the whole thing feel like one conversation:

- **AD-OE1** — The optimistic ACK ("Geht klar") is emitted *before* the worker dispatch
  returns, never after.
- **AD-OE2** — The Talker never `await`s an MCP/network call on the voice path. The
  Talker↔Worker queue is the in-process EventBus + mission event store — no external broker
  (the €5-VPS doctrine forbids a new hard dependency like Redis).
- **AD-OE3** — "Dumb" tools (local scripts) resolve in-process and must *not* wake a worker.
- **AD-OE4** — "Smart" tools: the worker issues the MCP call, never the Talker.
- **AD-OE5** — The Oops loop: a worker failure becomes a frozen `WorkerCorrectionNeeded`
  event, injected into the Talker's context, spoken *only* at the next Silero-VAD
  turn-boundary, through `scrub_for_voice`. It never interrupts mid-utterance.
- **AD-OE6** — Zero silent drops: every worker/MCP failure yields a silent retry, a spoken
  correction, or an audited apology. Silence is never an acceptable outcome.

Latency budgets are SLO-gated and regressions fail CI: p95 wake→ACK < 1.2 s, intent→ACK
< 3.0 s, router decision < 150 ms.

---

## 10. Phase Status (what is actually built)

| Phase | Status | What it delivers |
|---|---|---|
| **0–4 Foundations** | Live | Plugin system + protocols, FastAPI/React desktop app, speech pipeline, skill system, tool-use loop, risk-tier executor, core memory, harness dispatch. |
| **5 Vision/Action/Admin/Async/Control** | Live | Computer-Use enabled; tiered routing via `ROUTER_TOOLS`. |
| **6 Self-Healing Worker-Critic** | Live | Mission event store, manager, recovery, state machine, budget, workers, critic, kontrollierer, safety, isolation. Wired into REST + voice. |
| **7 Self-Mod** | Live | Config audit/writer/registry + three router tools (`list_mutable_settings`, `get_config_value`, `set_config_value`). Skill-author spawn pending registration. |
| **Awareness A0–A5** | Live | State, story, salience, working-set, episode, recall-store, watchers, probes. Tools `awareness-snapshot`, `awareness-recall`. Hard rule: never on the voice critical path. |
| **Knowledge Wiki** | Live | Curator, atomic writer, page repository, session rollup, voice bridge, scheduler. Tools `wiki-recall`, `wiki-page-read`, `wiki-ingest`. Long-term memory tier. |
| **Jarvis-Agents bridge** | Waves 1+4 | Harness contract (AD-1..21). Live-bridge-as-default (Welle 2) + full live mode (Welle 3) open. |
| **Ack-Brain** | Live | Sub-second butler ACK before the deep brain replies. |
| **CLI catalog + terminal** | Live | CLI discovery/install/probe + ConPTY terminal view. Tools `cli-tools`, `spawn-cli-worker`. |

### The Phase-6 isolation invariants (how workers stay safe)

- Every worker runs in a **fresh git worktree** (`agent/<task-slug>`) under a separate
  outputs directory — never in the user's working tree.
- Every worker subprocess is wrapped in a **Windows Job Object** with
  `KILL_ON_JOB_CLOSE` — no zombie processes if the orchestrator crashes.
- `MAX_CRITIC_LOOPS = 3` is hardcoded and not parameterizable.
- **The LLM never authors its own Observation** (ADR-0009). Voice readback reads only the
  Kontrollierer-signed `MissionApproved.summary_de`, never the Critic-LLM's raw correction
  text. This prevents an agent from grading its own homework.

---

## 11. Cross-Platform Desktop Features (the six ports)

Six features that were historically Windows-only are now cross-platform behind a shared
capability seam (`jarvis/platform/`): a `detect_platform()` probe plus a cached frozen
`Capabilities` snapshot. Each feature = one Protocol + one per-OS implementation + a
`sys.platform` factory + a graceful logged null-fallback.

| Feature | Windows | macOS | Linux |
|---|---|---|---|
| Terminal (PTY) | ConPTY (`pywinpty`) | `ptyprocess` | `ptyprocess` |
| App-launch | App Paths | `open -a` | `xdg-open`/exec |
| UI-element-click | UIA | AX (`pyobjc`) | AT-SPI (`pyatspi`) |
| Orb overlay | Tk color-key | Tk `-transparentcolor` | best-effort + tray |
| Hotkey | `global-hotkeys` | `pynput` | `pynput` (X11); Wayland no-op |
| Admin/elevation | UAC + SDDL pipe | Authorization Services | pkexec/sudo |

The headless €5-VPS base install ships **none** of these — every port is extras-gated and
degrades to a logged no-op when its capability is absent. The HMAC / Pydantic-argv /
`shell=False` security core (originally ADR-0001, now ADR-0020) is reused unchanged across
all platforms.

---

## 12. The Five Recurring Bug Classes (institutional memory)

These five patterns have each recurred multiple times. Recognizing the *signal* and applying
the *defense* is core project knowledge.

1. **Four-layer restore trap** (BUG-006 → -014 → -015). A fix "works in tests" but the live
   app behaves unchanged after restart, because the change landed in a worktree / stale
   frontend build / RAM bundle / an editable-install pinned to a deleted clone.
   *Defense:* `pwsh scripts/preflight.ps1` + `python -c "import jarvis; print(jarvis.__file__)"`.

2. **Multi-layer enum drift** (BUG-008, four episodes). A vocabulary string (e.g. a mission
   status or hangup reason) added in only one of its five layers (Python ↔ SQL ↔ Pydantic ↔
   TypeScript ↔ UI) causes an empty UI list, an HTTP 500, or a Pydantic `literal_error`.
   *Defense:* the five-layer pattern with a single source-of-truth constants file + a parity
   test, applied *preemptively* for any new wire-format enum.

3. **Config drift** (BUG-010, triple-defense). Parallel sessions rewriting `jarvis.toml`
   silently roll back each other's provider switches. *Defense:* a 5-minute drift-guard cron
   + ENV overrides + read-only TOML + a BOM-safe writer.

4. **Subprocess console flicker** (BUG-012). A `subprocess` call missing
   `NO_WINDOW_CREATIONFLAGS` causes a flicker storm under `pythonw.exe`. *Defense:* every new
   subprocess call imports the flag from `jarvis.core.process_utils`.

5. **Audio host-API blocking-write trap** (BUG-014). The auto-resolver picks WDM-KS, whose
   blocking write API crashes PortAudio, producing silent TTS. *Defense:* a forbidden-host-API
   filter + shortest-unique-token device matching.

---

## 13. The Anti-Pattern Register (do not do these)

A condensed selection of the codified "if you do X you get bug Y" rules:

- **Spawn a subprocess without `NO_WINDOW_CREATIONFLAGS`** → flicker storm (AP-1).
- **Accept an API key via voice/chat** → STT log leak, credential exfiltration (AP-2).
- **Call `Tool.execute()` directly** → risk tier / whitelist / plausibility skipped (AP-3).
- **Add a wire-format enum value in one site only** → multi-layer drift (AP-4).
- **Put a spawn tool in a worker tool set** → infinite recursion (AP-5).
- **Hardcode a Claude/Anthropic client** → breaks `cfg.brain.primary` (AP-6).
- **Write `jarvis.toml` without lock + tempfile + BOM handling** → corrupted TOML, boot fails (AP-7).
- **Add an LLM call inside `scrub_for_voice`** → TTS latency tank (AP-11).
- **Re-add a Jarvis-Agent tier** → breaks the Jarvis-Agents-bridge contract (AP-14).
- **Auto-activate generated skills** → lateral-movement vector; skills must stay `draft` (AP-15).
- **Run Jarvis as a Windows Service** → the SYSTEM user has no mic/headset access (AP-17).
- **Propagate a subscriber exception from the EventBus** → one handler kills the pipeline (AP-18).

---

## 14. Secrets & Configuration Hygiene

Secrets are accessed only via `get_secret(key, env_fallback)`. The hierarchy is: Windows
Credential Manager (service `personal-jarvis`) → ENV → `.env` (dev fallback only). API keys
**never** appear in code, `jarvis.toml`, commits, or `.claude/` files. The voice/chat path
must never accept a secret (it would be logged by STT).

All mutations of `jarvis.toml` go through `config_writer.py` only — `tomlkit`-based (preserves
comments), guarded by a write-lock mutex, BOM-aware, written via tempfile + atomic
`os.replace`. For Phase-7 self-mod the pipeline is non-negotiable: Allowlist → Read → Apply →
Pre-Validate → Backup → Tempfile+replace → synchronous reload-test → Rollback-on-fail →
`ConfigReloaded` dispatch → Backup GC → Audit.

---

## 15. Testing Philosophy

- **Fakes, not mocks.** Real fake implementations live in `tests/fakes/`; new STT/Brain/Tool/
  Channel providers must pass a shared contract test suite.
- **Layer-targeted regression guards** pin the hard-won fixes: 26-case router-discipline test,
  40-case voice-scrubber test, enum-parity tests, latency-regression tests.
- The full suite runs in `asyncio_mode=auto`; markers segment it (`phase5`, `e2e`,
  `voice_latency`, `eval`, `openclaw_live`, `slow`, `skip_ci`).

---

## 16. The One-Paragraph Summary

Personal Jarvis is a cloud-first, cross-platform voice meta-orchestrator built around a
Supervisor–Agent pattern: a fast Talker brain holds an uninterrupted spoken conversation and
optimistically acknowledges work, while self-healing background workers — each isolated in a
git worktree and a kill-on-close job object, graded by a Critic loop and signed by a
Kontrollierer — execute against interchangeable harnesses. Five swappable provider classes
(Brain, STT, TTS, Vision, Wake) each have a fully cloud-reachable default, so the entire
experience runs on a €5 VPS in a browser, with the maintainer's GPU workstation treated as an
opt-in power-user profile. Every layer talks to the next only through protocols and an
immutable event bus, every risky action passes a single risk-tier chokepoint, and a deep
register of recurring bug classes and anti-patterns is encoded directly into the tests so the
same mistake is never shipped twice.
