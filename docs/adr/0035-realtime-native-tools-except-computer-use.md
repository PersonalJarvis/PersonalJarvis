# ADR-0035 — Realtime native tools: the live model calls every Jarvis tool itself; the Tool Model is for computer use

**Status:** Accepted · **Date:** 2026-08-19 · **Phase:** Voice UX, realtime engine (every transport, every provider)

## Context

Maintainer mandate (2026-08-19): the realtime voice model must be allowed to
call *every* Jarvis tool directly — except computer use. The Tool Model
(`[brain.tool_model]`, historically `cu_model` / `[brain.computer_use]`) was
introduced for computer use and became the delegate for every action turn
along the way. That generalisation is what the user hears as "no
conversational flow": the live model is fast, the delegate is not.

Measured 2026-08-17..19 (flight recorder, 74 realtime turns, vertex-live):

- 55 of 74 turns involved a delegation (31 planner-forced, 11 empty-turn
  recoveries, 13 model-requested).
- First audio: native median **0.9 s**; delegated median **7.2 s**
  (p25 4.4 s, p75 12.4 s). Delegation alone: median 5.6 s.
- Every delegated brain round carries **18–46k input tokens** (system prompt,
  87 tool schemas, context, history) — a 3–6 s floor before the delegate has
  said a word, even when it answers "Hello".
- Mis-routed conversation: "sprich mal mit mir" 3.2 s, "was würdest du
  empfehlen" 15.6 s, "Wer waren die 10 berühmtesten Wissenschaftler?" 5.6 s,
  "gucken, was morgen für ein Tag ist" 5.5 s (the same question without
  "gucken" answered natively in 0.9 s).  <!-- i18n-allow: quoted live utterances -->

What the live model can do today: **nothing**. In the default `delegate` tool
mode it has exactly one function, `jarvis_action`, and `end_call`. The former
`direct` mode declared the whole dynamic catalog and was demoted to a
diagnostic opt-in (BUG-051 §5): a live reproduction on OpenAI Realtime showed
134 declarations, ~26k requested input tokens per response and a 40k TPM
limit — one turn before rate limiting. Today's catalog is 87 tools, ~78k
characters of declarations (~15–20k tokens under compaction). The execution
path for native calls — `RealtimeToolBridge` → `SupervisorToolGateway` /
`ToolExecutor`, risk tiers, two-turn voice confirm, STT/spawn/CU/research
guards — exists, is tested, and is unchanged by this ADR.

## Decision

One provider-neutral design (CLAUDE.md §3). The behaviour is the same on
every transport; transport-specific mechanisms are declared capabilities.

### 1. New default tool mode: `hybrid`

`[voice].realtime_tool_mode` gains the value `hybrid` and it becomes the
default. `delegate` and `direct` stay as explicit choices (unknown values
fail closed to `delegate`, as before).

In `hybrid` the live model receives **three kinds of functions**:

1. the supervisor tool catalog, **minus the computer-use vehicles**
   (`jarvis.brain.cu_gate.CU_VEHICLE_TOOL_NAMES`), rendered compactly (§4);
2. `jarvis_action` — narrowed to (a) operating the computer on screen
   (click, type, navigate inside application windows until a multi-step
   desktop task is done), (b) looking at the screen, and (c) any Jarvis
   capability for which the model has **no function of its own** (a tool
   dropped under the declaration budget, or one that connected after the
   session started);
3. `end_call`.

The role directive changes accordingly: *the user's world is reached with
your own functions, immediately; the screen is reached through
`jarvis_action`.* The announce-without-acting rule, the never-invent-results
rule, the several-targets rule and the spoken-reply rendering are unchanged.

### 2. What stays deterministic (delegate-owned) in `hybrid`

The planner (`jarvis/brain/turn_planner.py`) keeps classifying every final.
Its `requires_orchestrator` verdict no longer forces a delegation; it becomes
the turn-mode hint *"this turn needs the user's world — use your functions
now"*. A delegation is still forced for exactly these classes:

- **computer use:** an explicit harness mention ("computer use") or a desktop
  ACTION verb together with a desktop SURFACE noun (`cu_gate`
  `_DESKTOP_ACTION_RE` ∧ `_DESKTOP_SURFACE_RE`, i.e. narrower than "CU may
  run"); a bare action verb without a surface ("öffne Spotify") stays with
  the live model and its functions;  <!-- i18n-allow: quoted utterance -->
- **screen look:** `TurnReason.SCREEN_CONTEXT` — the supervisor captures and
  attaches the one-shot image the live model cannot see;
- **public-fact grounding** when the active provider declares
  `requires_public_fact_grounding` (unchanged);
- **pending two-turn confirmations and open delegate questions**
  (`_brain_awaits_voice_confirm`, `_answers_open_delegate_question`) —
  these answer a turn the delegate already owns.

Everything else — Wiki, settings, connected data, media, skills, missions,
coding panes, files, CLI/MCP/plugin tools — is the live model's own call.

### 3. Slow native calls get the instant ack

A native function call is blocking on every current transport (ADR-0034 §2:
`NON_BLOCKING` is unsupported on Vertex and on the 3.1 Live model; OpenAI
waits for `function_call_output`). The ADR-0033 rule therefore applies to
native tool calls as to delegated turns: short work stays chatter-free for
`SHORT_GRACE_S` (3 s); a longer call gets ONE ack line from the closed pool
of the work class the planner assigned the turn (tool-activity pool as the
fallback), at most once per turn (`_native_tool_ack_after_grace`). Because
the live model is blocked on the call, the line rides the surface status
channel BUG-070 established — moving it into the live voice is the same
follow-up ADR-0034 §2.4 names. The result is spoken when it lands; a new
user turn while a call is open takes the ADR-0034 unblock path
(`_PENDING_TOOL_CALL_INTERIM_RESULT`) and the real result is parked.

### 4. Declaration budget and compaction

Declarations are rendered compactly for the live model: tool description
capped (default 450 characters, a sentence boundary preferred), parameter
descriptions capped (default 120 characters), schema otherwise intact.
`[voice].realtime_tool_declaration_budget_tokens` (default 20 000; estimate
= characters / 4) bounds the whole set. Over budget, tools are dropped in a
deterministic priority order until the set fits — longest declaration first
within the lowest-priority family (`agentic-ide-*` when no workspace is
open, then `cli_*`, then the rest) — and **every dropped name is logged at
session start**. A dropped tool is not lost: it is reachable through
`jarvis_action` (§1.2c), whose delegate keeps the full catalog.

A provider may declare `tool_declaration_budget_tokens` (capability, AP-21)
to lower the budget for its own wire: `openai-realtime` and `local-realtime`
(OpenAI protocol, TPM-metered per response) declare 8 000 unless the config
overrides it; `gemini-live` / `vertex-live` declare none (the setup is sent
once per connection). Transports with `supports_direct_tools = False` stay in
the deterministic delegate mode — nothing to declare on that wire.

### 5. Session-static declarations

Gemini Live cannot change tools mid-session (`supports_tool_updates =
False`): a CLI, MCP or plugin that connects during a call is reachable via
`jarvis_action` until the next session, and the existing warning says so.
OpenAI-protocol transports take the refreshed declarations through
`session.update` (unchanged).

### 6. Provider × mode matrix

| Transport | `hybrid` (default) | `delegate` | `direct` |
|---|---|---|---|
| `vertex-live`, `gemini-live` | catalog − CU, + `jarvis_action` (CU/screen/fallback), full budget | as today | full catalog, no `jarvis_action` (diagnostic) |
| `openai-realtime`, `local-realtime` | same set under the 8k-token provider budget; over-budget tools via `jarvis_action` (logged) | as today | as today (diagnostic) |
| `supports_direct_tools = False` (handoff) | degrades to `delegate` with the stated reason | as today | forced `delegate` (as today) |
| Classic pipeline, text chat, channels, CLI | unaffected — the brain already holds its tools; CU stays behind `computer_use` + its gate | — | — |

### 7. Observability

- Session start logs: mode, declared count, token estimate, dropped names.
- `REALTIME_TOOL_COMPLETED` latency span per native call (exists) gains
  `duration_ms`; postmortem counters `native_tool_calls`,
  `native_tool_failures`, `native_tool_denied`, `delegate_cu_dispatches`.
- Postmortem `delegate_*` counters keep their meaning; a drop in
  `delegate_deliveries_completed` with steady `turns_completed` is the
  expected signature of this change.

### 8. Guards

- Every descriptor of the live catalog passes the Gemini schema sanitizer
  (`_sanitize_declarations`) and the OpenAI declaration shape — a unit test
  iterates the seeded catalog plus the native tool plugins.
- `hybrid` session declares `jarvis_action` + `end_call` + catalog − CU; CU
  vehicle names are never declared natively (drift guard).
- The forced-delegation classifier: the live mis-routes above stay native;
  "klick auf den Button", "mach das Fenster zu", "mit computer use …" force
  delegation; "öffne Spotify" does not.  <!-- i18n-allow: quoted utterances -->
- Budget: a synthetic 200-tool catalog is trimmed deterministically, dropped
  names logged, `jarvis_action` still declared.
- Parity: a transport with ``supports_direct_tools = False`` never sees the
  hybrid role directive — the session falls back to `delegate` before the
  first instruction is built (`test_hybrid_falls_back_to_delegate_when_a_provider_cannot_declare_tools`),
  so the existing handoff parity test keeps covering the only directive such
  a transport receives.

## Consequences

- Simple actions ("play X", "what is in my calendar", "remember that",
  "switch the provider") move from the 5–8 s delegate path to a 1–3 s native
  call. Conversation and world knowledge stay at ~1 s.
- Per-turn token usage on Gemini Live rises by the declaration block (on the
  order of 15k tokens of persistent context); that is the price of the
  mandate and it is paid once per connection in prefill, not per delegate
  round. On TPM-metered wires the budget keeps it bounded.
- Multi-step chains are now the live model's own rounds. Where the Flash
  live model chooses badly, the guards (STT hallucination, instructional,
  spawn, CU, research) still refuse; a wrong but allowed call is visible in
  the postmortem counters and the tool log. The Tool Model remains the
  orchestrator for computer use, where a wrong click costs the most.
- The delegate stays fully functional: empty-turn recovery (step 1 of the
  2026-08-19 plan), planner precision (step 2) and a slimmer delegated prompt
  (step 3) still apply to the turns that delegate.

## Alternatives considered

- **Curated fast lane (6–10 native tools).** Smaller prompt, but a second,
  hand-maintained tool list drifts from the catalog and contradicts the
  mandate ("everything but computer use").
- **Keep delegate, make it faster.** Trimming the delegated prompt helps
  every delegation (and is still done), but the round trip through a second
  model keeps a 3 s floor plus readback; the fast path has to be native.
- **Full `direct` as default.** Declares CU to the live model and has no
  fallback for over-budget or late-connected tools; rejected for the same
  reason it was demoted in BUG-051.
