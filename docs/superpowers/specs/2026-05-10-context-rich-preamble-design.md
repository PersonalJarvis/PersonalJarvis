# Context-Rich Pre-Thinking Acknowledgment — Design Spec

**Date:** 2026-05-10
**Status:** Brainstorm complete, awaiting plan
**Owner:** AlexMaintainer (driver) + Claude (architect)
**Supersedes:** Implicit "perceived-latency-reduction pattern" from `jarvis/brain/ack_generator.py:1-29` (template-based)
**Cross-references:** ADR-0011 (Router-Discipline), AD-7 anti-pattern (constraint enforcement in code), `docs/jarvis-agents-bridge.md` (MissionAnnouncer pattern)

---

## 1. Goal & Non-Goals

### Goal

Replace the existing template-based, per-tool acknowledgment generator with a context-rich variant that produces one short, substance-bearing sentence referencing the user's actual request — spoken **before** Jarvis begins deep reasoning or tool execution.

The user must hear, within ~1 second of finishing their utterance, that Jarvis (a) understood the request, (b) knows what topic he's about to work on, and (c) has set the right execution path.

### Concrete before / after

```
Today (template):
  User:   "Hey Jarvis, ich möchte morgen eine Reise nach San Francisco buchen,  <!-- i18n-allow: quoted German voice-input example -->
           kannst du Flugdaten raussuchen?"
  Jarvis: "Verstanden, ich kümmere mich darum."        ← generic, swappable  <!-- i18n-allow: quoted German voice-output example -->

Goal (context-rich):
  User:   [same input]
  Jarvis: "Ja Chef, ich gebe das an den Jarvis-Agent weiter,  <!-- i18n-allow: quoted German voice-output example -->
           melde mich gleich mit den Flugdaten."        ← topic + handoff +  <!-- i18n-allow: quoted German voice-output example -->
                                                          time-horizon
```

### Non-Goals

- **Not** introducing a new TTS provider, audio path, or pipeline state.
- **Not** changing the final-answer flow (the brain's eventual response after tool execution stays as today).
- **Not** adding LLM-call latency to direct-action tools that already feel responsive (we piggyback on the call that's running anyway).
- **Not** making the preamble path the only channel — `MissionAnnouncer` post-completion summaries continue to use the same `AnnouncementRequested` bus event.

---

## 2. User Stories

**US-1 (heavy task, the core motivating case):**
> As Alex, when I ask Jarvis to research a non-trivial topic, I want to hear within a second that he started the right kind of work, with the topic explicitly named — so the 5-30 second gap until the real answer feels like deliberate work, not silent confusion.

**US-2 (direct action):**
> As Alex, when I ask Jarvis to open an app or look up a quick fact, I want a one-sentence confirmation that names the action — so even fast operations feel grounded and confirmed, not robotic.

**US-3 (silence is golden):**
> As Alex, when I just say "Hallo" or tell Jarvis to be quiet, I do **not** want any preamble — those interactions are too lightweight to deserve a multi-stage response.

**US-4 (failure is silence):**
> As Alex, when the smart preamble can't be generated cleanly (hallucination, timeout, content-filter strip), I prefer Jarvis to stay silent and go straight to the real answer over hearing a generic filler. "Verstanden, ich kümmere mich darum." ("Understood, I'll take care of it.") across every request was exactly the failure mode of the previous attempt. <!-- i18n-allow: quoted German voice-output example -->

---

## 3. Architecture

### Approach: Router-Extended

The existing Router-Brain (Haiku 4.5) already runs a tool-choice classification call on every utterance. We extend that single call to **co-emit the preamble** in the tool's args. Zero added round-trips. The brain has the full utterance context and the tool decision in the same response, so the two are coherent by construction.

Rejected alternatives:

- **B. Parallel Haiku call** — adds 400-700 ms to first-audio time and doubles token cost.
- **C. Slot-fill + LLM hybrid** — two divergent code paths that drift over time.
- **D. Streaming-first-sentence** — would re-introduce filler-opener patterns that the existing `scrub_for_voice` blacklist explicitly removes (40 test cases). High drift risk.

### Data Flow

```
                User: "Hey Jarvis, find SF flights for tomorrow"
                                  |
                                  v
                       [STT: faster-whisper]
                                  |
                Final: "find SF flights for tomorrow"
                                  |
                                  v
                       [BrainManager.generate]
                                  |
                                  v
            ┌──── Router-Brain Call (Haiku 4.5) ────┐
            │                                       │
            │   Input: utterance + Router prompt    │
            │   Output: tool_call(                  │
            │     "spawn_worker",                   │
            │     args={                            │
            │       task: "find SF flights ...",   │
            │       preamble_de: "Ja Chef, ...",   │  ← NEW
            │       preamble_en: "Got it, ...",    │  ← NEW
            │     })                                │
            │                                       │
            └──────────────┬────────────────────────┘
                           |
                           v
            ┌──── Brain.tool_use_loop ─────────────┐
            │                                       │
            │   IF args has preamble_<lang>         │
            │   AND tool not in ACK_SKIP_TOOLS      │
            │   AND not is_voice_control(utterance) │
            │   AND not invocation_is_nested:       │
            │      publish AnnouncementRequested    │
            │   THEN execute tool with cleaned args │
            │                                       │
            └──────────────┬────────────────────────┘
                           │ (tool runs in background:
                           │  spawns the Jarvis-Agent worker, takes 5-30 s)
                           │
                           │      ┌──── Speech-Pipeline ────┐
                           │      │                         │
                           │      │   _on_announcement:     │
                           │      │   scrub_for_voice       │
                           │      │   -> TTS synthesize     │
                           │      │   -> player.play_chunks │
                           │      │                         │
                           │      └────────────┬────────────┘
                           │                   │
                           │                   v
                           │        "Ja Chef, ich gebe das
                           │         an den Jarvis-Agent weiter ..."  <!-- i18n-allow: quoted German voice-output example -->
                           │           (spoken via TTS)
                           │
                           v
              Final response after tool done:
              "Hier sind drei Flüge ..." (spoken via TTS,  <!-- i18n-allow: quoted German voice-output example -->
                                         + new chat bubble)
```

### Three Critical Properties

**P1 — Latency-neutral.** The preamble emerges in the same Router-Brain round-trip as tool selection. Today's call is ~200-400 ms; with the preamble field added it's ~250-500 ms. Practically imperceptible. The TTS synthesis of the preamble adds 300-800 ms — the same cost as the existing template path.

**P2 — Audio order guaranteed.** The preamble is published with `priority="normal"`, queueing it at the head of the TTS queue. The tool runs in parallel; when its final response is ready (5-30 s later), it appends to the queue. There is never overlap or interruption — except for genuine barge-in (`"sei still"`), which uses `priority="interrupt"` and stops both.

**P3 — Blacklist-compliant.** The preamble passes through `scrub_for_voice` like any other voice output. Tool-call leaks, stack traces, markdown residues, and the `"Sir"` form of address are filtered. `"Jarvis-Agent"` is allowed (already used in `MissionAnnouncer`). `"Subagent"` / `"Sub-Agent"` / `"Worker"` remain blocked — the system prompt explicitly instructs the LLM to use `"Jarvis-Agent"` or the first-person `"ich"` (I) instead. (2026-05-24 update: `scrub_for_voice` now actively strips the retired "OpenClaw" brand token from voice output — this design predates that change.)

---

## 4. Components

### Backend

```
jarvis/brain/factory.py                        [MODIFY]
  Central wrapper around _load_tools_for_tier injects preamble_de
  and preamble_en (both optional in JSON Schema, both strongly
  required by system-prompt rule). Wrapper skips tools listed in
  ACK_SKIP_TOOLS so click/type_text/awareness_snapshot don't get
  the fields. Router system prompt extended with style rules and
  one bilingual example.

jarvis/brain/tool_use_loop.py                  [MODIFY]
  Before executing a tool call:
    1. Extract preamble_de / preamble_en from tool args.
    2. Strip them from args before passing to tool implementation
       (the tool's input_schema has the fields, the tool function
       doesn't — they're decoration, not real args).
    3. Determine language (from session / detected).
    4. If args had a preamble for that language AND tool not in
       skip-list AND utterance is not voice-control AND we are
       not nested inside another spawn_worker frame:
       publish AnnouncementRequested.
    5. Execute tool with cleaned args.

jarvis/brain/ack_generator.py                  [REFACTOR]
  Keep:
    - ACK_SKIP_TOOLS frozenset
    - is_voice_control_utterance()
    - final_summary_marker()  (for "Erledigt." marker)
    - should_prepend_marker() (Erledigt. logic)
  Remove:
    - All per-tool template handlers (_ack_search_web,
      _ack_open_app, _ack_spawn_sub_jarvis, etc. — ~12 funcs)
    - _GENERIC_ACK fallback dict (no fallback by design)
  Replace generate_ack signature so it becomes a thin lookup:
    def generate_ack(tool_name, tool_args, *, language="de"):
        if not tool_name or normalize(tool_name) in ACK_SKIP_TOOLS:
            return None
        text = (tool_args or {}).get(f"preamble_{normalize_lang(language)}")
        return text.strip() or None if isinstance(text, str) else None

jarvis/brain/output_filter.py                  [VERIFY-ONLY]
  No code change required; "Jarvis-Agent" is already not on the
  blacklist. Add a regression test that confirms it survives
  scrub_for_voice unchanged. (2026-05-24 update: the blacklist
  now actively strips the retired "OpenClaw" brand token instead
  of allowing it through — this design predates that change.)

jarvis/core/events.py                          [MINOR]
  Optionally add `kind: Literal["preamble", "completion", "info"]
  | None = None` field to AnnouncementRequested (default None to
  preserve backwards compat). Used by the UI to render preamble
  bubbles distinctly from regular announcements.
```

### UI / Frontend

```
jarvis/ui/web/server.py                        [SMALL]
  AnnouncementRequested events with kind=="preamble" are
  forwarded to the chat WebSocket as a new message-role so the
  React frontend can render them as a separate bubble.

jarvis/ui/web/frontend/src/types/messages.ts   [SMALL]
  MessageRole = "user" | "jarvis" | "preamble"

jarvis/ui/web/frontend/src/views/.../ChatView.tsx
                                                [MODIFY]
  Render preamble role with subtler styling (muted color,
  italic) and a "preamble" chip so user knows this is the
  pre-thinking acknowledgment. Bubble stays in history.
```

### Tests

```
tests/unit/brain/test_ack_generator.py         [REWRITE, ~12 cases]
tests/unit/brain/test_routing.py               [EXTEND, ~6 cases]
tests/integration/test_preamble_flow.py        [NEW, ~6 cases]
tests/contract/test_brain_protocol.py          [EXTEND, ~3 cases]
scripts/smoke-test-preamble.ps1                [NEW, manual]
```

---

## 5. Behavior Matrix

| User Input                       | Router Decision                | Preamble Fires? | Notes / Example Output                                           |
|----------------------------------|--------------------------------|-----------------|------------------------------------------------------------------|
| `"Hallo Jarvis"` ("Hello Jarvis")                 | smalltalk (no tool)            | **No**          | Direct brain answer; no tool args, so no preamble field exists.  |
| `"Sei still"` ("Be quiet")                    | n/a (voice-control)            | **No**          | `is_voice_control_utterance` → True. Audio stops.                |
| `"Mach Spotify auf"` ("Open Spotify")             | `open_app(name=Spotify)`       | **Yes**         | "Mach ich, Spotify öffnet sich." ("On it, Spotify is opening.")  | <!-- i18n-allow: quoted German voice example -->
| `"Wie ist das Wetter?"` ("What's the weather?")          | `search_web(query=...)`        | **Yes**         | "Schau ich nach, einen Moment." ("Checking now, one moment.")    | <!-- i18n-allow: quoted German voice example -->
| `"Find SF flights tomorrow"`     | `spawn_worker(task=...)`       | **Yes**         | "Ja Chef, ich gebe das an den Jarvis-Agent weiter, melde mich gleich." ("Yes, handing that off to the Jarvis-Agent, I'll be right back.") | <!-- i18n-allow: quoted German voice example -->
| `"Klick auf Speichern"` ("Click Save")          | `click(target=...)`            | **No**          | `click` in ACK_SKIP_TOOLS — chattering on UI events forbidden.   | <!-- i18n-allow: quoted German voice example -->
| `"Was läuft gerade?"` ("What's running right now?")            | `awareness_snapshot()`         | **No**          | Passive read in skip-list.                                       | <!-- i18n-allow: quoted German voice example -->
| LLM omits `preamble_de`          | spawn_worker                   | **No**          | Silent fallback. `preamble_skipped_no_field_total` ++.           |
| LLM emits 50-word preamble       | spawn_worker                   | **Yes, cut**    | First sentence kept, rest dropped. `preamble_truncated_total` ++.|
| LLM emits `"Ich starte den Subagenten"` ("I am starting the subagent") | spawn_worker | **No** | scrub_for_voice strips "Subagent" → empty after scrub → silent.  |
| Tool-inside-Jarvis-Agent (nested) | n/a — caller is worker         | **No**          | Recursion guard: only top-level main-Jarvis tool calls preamble. |

---

## 6. Failure Handling

| ID  | Scenario                                          | Behavior                                                       | Telemetry counter                       |
|-----|---------------------------------------------------|----------------------------------------------------------------|-----------------------------------------|
| F1  | LLM omits `preamble_de` field entirely            | `args.get(...)` → None → silent                                | `preamble_skipped_no_field_total`       |
| F2  | LLM emits empty / whitespace string               | Treated like F1                                                | `preamble_skipped_empty_total`          |
| F3  | LLM emits > 30 words                              | Cut at first `[.!?]` boundary, rest dropped                    | `preamble_truncated_total`              |
| F4  | LLM emits banned compound (`Subagent`, `Worker`)  | scrub_for_voice strips → empty → silent                        | `preamble_scrubbed_empty_total`         |
| F5  | LLM emits wrong-language preamble                 | Use the field that matches detected language; warn-log         | `preamble_lang_mismatch_total`          |
| F6  | Router-Brain call timeouts / errors               | Existing BrainManager error path; no tool, no preamble         | (existing infrastructure)               |
| F7  | TTS synthesis fails on preamble                   | `_on_announcement` logs warning; final answer still attempts   | `preamble_tts_failed_total`             |
| F8  | User barge-in mid-preamble                        | `priority="interrupt"` cancels preamble TTS; existing barge-in | (existing infrastructure)               |

**Design principle:** silent failure is preferred over generic-template fallback. Generic templates were exactly the user-rejected behavior; reverting to them on failure would re-introduce the problem this spec is solving.

---

## 7. Telemetry

Counters published via the existing flight-recorder bus:

```
preamble_emitted_total{tool_name=...}
preamble_skipped_skip_list_total{tool_name=...}
preamble_skipped_voice_control_total
preamble_skipped_smalltalk_total
preamble_skipped_no_field_total{tool_name=...}
preamble_skipped_empty_total{tool_name=...}
preamble_truncated_total{tool_name=...}
preamble_scrubbed_empty_total{tool_name=...}
preamble_lang_mismatch_total{tool_name=...}
preamble_tts_failed_total
```

A `preamble_skipped_no_field_total / preamble_emitted_total` ratio above 5 % is the operational alarm — it means the LLM is not honoring the prompt rule and we need to tighten the prompt or fall back to a stronger model.

---

## 8. Test Strategy

### Unit (`tests/unit/brain/test_ack_generator.py`, ~12 cases)

- `generate_ack` returns `args["preamble_de"]` when language is `de` and value present
- `generate_ack` returns `args["preamble_en"]` when language is `en` and value present
- `generate_ack` returns `None` when tool in `ACK_SKIP_TOOLS`
- `generate_ack` returns `None` when args is `None` or missing the language-specific field
- `generate_ack` returns `None` when the field is `""` or pure whitespace
- `generate_ack` truncates preambles longer than 30 words at first sentence boundary
- `generate_ack` returns `None` when scrub_for_voice would strip everything
- `is_voice_control_utterance` regression coverage retained (existing 5+ cases)
- `final_summary_marker` and `should_prepend_marker` behavior unchanged
- `ACK_SKIP_TOOLS` membership regression guard
- Tool name normalisation (hyphen vs. underscore) preserved
- Language normalisation (`de`, `en`, `de-DE`, `en-US`, garbage) preserved

### Unit (`tests/unit/brain/test_routing.py`, ~6 new cases)

- Router system prompt contains the preamble-instruction snippet (substring assertion)
- Tool schemas wrapped in `factory._load_tools_for_tier` include `preamble_de` and `preamble_en` in `properties`
- Wrapped fields are optional, never in `required`
- Tools listed in `ACK_SKIP_TOOLS` are NOT wrapped (no preamble fields on click / type_text / etc.)
- Wrapper preserves all original required fields of the tool
- Wrapper is idempotent (calling twice produces the same shape)

### Integration (`tests/integration/test_preamble_flow.py`, ~6 new cases)

- Happy path: `spawn_worker` with preamble args → `AnnouncementRequested` published → `_on_announcement` invoked → TTS called once with the preamble text
- Voice-control bypass: `"sei still"` short-circuits before any tool selection; no preamble emitted
- Skip-list: `click` tool returns no preamble even if args contain one
- Silent fallback: tool call missing `preamble_de` → no `AnnouncementRequested`, tool still executes
- Audit-log ordering: Brain audit log shows preamble entry timestamp strictly before final-response entry
- Recursion guard: a tool call originating from inside a `spawn_worker` execution context emits no preamble

### Contract (`tests/contract/test_brain_protocol.py`, ~3 new cases)

- Brain.generate response shape allows `preamble_de` and `preamble_en` keys inside tool args without parser failure
- ToolDefinition schema after factory wrap accepts the wrapped form (round-trip JSON-Schema validation)
- All registered tools (entry_points discovery) load through the wrapper without raising

### Smoke (`scripts/smoke-test-preamble.ps1`, manual)

Five scripted utterances per language replayed via simulated STT-final, audio recorded, manual playback verifies:

1. Preamble + final are temporally separated, no overlap
2. Preamble references correct topic
3. No "Subagent" / "Worker" in audio
4. Voice-control utterance produces zero preamble audio
5. Smalltalk produces zero preamble audio

---

## 9. Open Questions / Future Work

- **Q1.** Should preamble emission also be wired into the `phase6` mission system (Worker Jarvis-Agents under `jarvis/missions/`)? Initial scope says no — those have their own `MissionAnnouncer` for completion announcements. Pre-thinking acks on internal mission steps would chatter.
- **Q2.** Should the user be able to disable preambles globally via a config flag (`[speech].pre_thinking_ack = false`)? Probably yes, but YAGNI for the first ship.
- **Q3.** Telemetry: do we want the preamble counters to roll up into a Grafana / dashboard view, or is the in-memory bus + JSON-Lines flight recorder enough for now? Defer to operations.
- **Q4.** Persona variation: today the prompt rule says "open with 'Ja Chef' in ~30 % of cases". After two weeks of usage we should sample the actual distribution and tune. Initial value is a guess.

---

## 10. Cross-References

- **ADR-0011 (Router-Discipline)** — `docs/adr/0011-router-pure-dispatcher.md`. The preamble field lives **inside** tool args (not at top-level message content) precisely to preserve Router-Discipline's "no narrative output" property. The LLM cannot use the preamble field as a covert text channel — it is structurally bound to a concrete tool selection.
- **AD-7 anti-pattern** — `CLAUDE.md` Phase-7 section. We partially relax AD-7 by letting the LLM generate the preamble *substance*, but enforce the *shape* (length, vocabulary, blacklist compliance) in code. A hybrid that preserves AD-7's intent (no constraint self-bypass) while gaining substance.
- **`MissionAnnouncer`** — `jarvis/missions/voice/announcer.py`. The downstream `AnnouncementRequested` → `_on_announcement` → TTS path is reused unchanged. This spec adds a new producer of that bus event; the consumer side is identical.
- **Output Filter** — `jarvis/brain/output_filter.py:scrub_for_voice`. The 40-case blacklist applies to the preamble. Specifically: `"Subagent"` is blocked, `"Jarvis-Agent"` is allowed. (2026-05-24 update: the retired "OpenClaw" brand token is now actively stripped instead — this design predates that change.)
- **BUG-006 / BUG-014** — When deploying, remember the four-layer restore trap (`docs/BUGS.md`). The editable install can pin to a stale clone, in which case the preamble feature ships in code but not at runtime. Verify with `python -c "import jarvis; print(jarvis.__file__)"` after install.
