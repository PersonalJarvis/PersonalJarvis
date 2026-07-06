# Contextual Interim Voice — Design Spec (v2 of the interim-ack redesign)

Date: 2026-07-06
Status: approved (maintainer request, autonomous session)
Supersedes the phrase-pool half of
`2026-07-06-interim-ack-redesign-design.md` (its gates stay).

## Problem

The v1 redesign replaced ONE canned phrase with POOLS of canned phrases. The
maintainer rejected that as still-standardized: the blue interim lines in the
transcription view must be **smart, elegant, tied to the actual request** —
"an HTML file about X" should earn a fresh, topic-aware line, not a rotation
of stock sentences. Additional requirements, verbatim intent:

- **Context-dependent, not standardized** — composed for THIS turn.
- **Rare** — speak only when genuinely necessary; today they fire far too
  often and "talk over" the main answers.
- **Fast** — the context analysis must not add latency to the turn.
- **Bug-free** — no triple spawns, no talking over the answer, and a hard
  stop against the historical infinite-repeat loop.
- **Every OS / every install** — a keyless downloader still gets a working
  (deterministic) fallback; no key, no GPU, no OS API required.

## Key insight

The codebase already ships the exact engine this needs:
`jarvis/voice/contextual_readback.py::ReadbackComposer` — the maintainer's
"no fixed stock phrases" mandate engine (bounded flash-LLM call, honesty
guards, language check, no-repeat memory, canned fallback, cross-family
failover, never raises). The `BrainManager` already holds an instance
(`self._readback_composer`, wired by `jarvis.brain.factory`). The v2 design
plugs the grounded tool-ack into that engine instead of building anything new.

## Design

### 1. Context-composed interim line (manager emitter)

`BrainManager._build_tool_ack_emitter` keeps its guards (voice-control
bypass, skip-list, once-per-turn, `grounded_ack_min_gap_s`) and changes the
text source:

- A new pure helper `describe_tool_action(tool_name, tool_args)` (in
  `ack_generator.py`) renders a compact **English** action description from
  the tool call — "running a web search for …", "querying GitHub",
  "fetching the user's email". English because it is prompt input, not
  product surface; the composer answers in the resolved turn language.
- The emitter composes via `render_readback(self._readback_composer, …)`
  with `instruction` = "you just started <action> for the user's request and
  it is taking a moment — say what you are doing right now", `facts` =
  {user_request, current_action}, `in_progress=True`, and a tight
  `latency_budget_ms` (1200). The v1 phrase pool becomes the `canned`
  fallback — spoken ONLY when the flash path is absent (keyless install),
  the breaker is open, the call times out, or the output is rejected.
- **Zero turn latency:** when the composer is wired, composition + publish
  run in a fire-and-forget task; the tool-use loop is never delayed. Without
  a composer the deterministic path publishes synchronously (sub-ms),
  preserving the existing test/behavior contract.
- Kill switch: new `[ack_brain].contextual_interim` (default true). False =
  deterministic pools only (v1 behavior).

### 2. Necessity = actual wait (config default changes)

The interim line must only speak when a real wait materializes. The v1
commit-grace gate already implements the mechanism (hold the announcement;
speak only if the turn is STILL processing afterwards); v2 tunes the policy:

- `grounded_ack_commit_grace_ms`: 900 → **2500**. Combined with tool
  selection (~3–5 s into a deep turn) the line speaks ~6–8 s in — turns that
  answer sooner stay completely silent.
- `grounded_ack_min_gap_s`: 20 → **30**.

### 3. Anti-loop backstop (speech pipeline)

The historical "said it forever" bug class gets a structural circuit: the
pipeline counts SPOKEN preamble/progress lines in a rolling 60 s window and
drops anything beyond `preamble_rate_limit_per_min` (new knob, default 3,
0 disables) with a warning log. Completion/interrupt readbacks are exempt
(owed answers). This caps ANY runaway emitter — present or future — at the
last shared chokepoint.

### 4. Unchanged / reused

- Spawn announcements (`SpawnAnnouncementComposer`) are already contextual
  LLM-composed with pool fallback — untouched (they are the model v2 follows).
- v1 gates stay: duplicate-wording dedup, JARVIS_SPEAKING stale-drop,
  floor/deferral, hangup/mute, `should_play` staleness predicate.
- `grounded_tool_ack=false` still disables the feature entirely.

## Cross-platform / keyless contract (CLAUDE.md §3)

- No new dependency, no OS API, pure asyncio — identical on Windows/macOS/
  headless Linux.
- Keyless install: `_readback_composer` is fallback-only or None → the
  deterministic pool line is used; every gate still applies. Feature never
  bricks a turn (compose task swallows all exceptions; tool execution is
  never blocked).

## Testing

- `describe_tool_action`: per-family descriptions, salient-arg extraction,
  garbage-args safety.
- Emitter: contextual path publishes the composed line (fake composer);
  composer failure falls back to the pool line; no composer → synchronous
  deterministic publish (existing tests keep passing); tool loop not blocked
  (emit returns before publish when composing).
- Pipeline: rate-limit backstop (4th distinct preamble within 60 s dropped,
  completions exempt); config 0 disables.
