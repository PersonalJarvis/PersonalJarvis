# Pre-Thinking Acknowledgment — Flash-Brain Design

**Date:** 2026-05-11, persona-prompt section revised 2026-05-13
**Status:** Brainstorm complete, persona prompts v2
**Owner:** AlexMaintainer (driver) + Claude (architect)
**Supersedes:** `docs/superpowers/specs/2026-05-10-context-rich-preamble-design.md` (Router-Extended approach — explicitly rejected by driver in favor of a separate, provider-pluggable Flash-Brain)
**Revision history:**
- 2026-05-13: §1 example, §4 persona-prompts, and §5 behavior matrix rewritten. The original §4 used 8 few-shot examples per language; in practice this caused mode-collapse: the LLM reproduced the example phrases ("Lass mich kurz nachschauen", "Mache ich") regardless of whether they fit the actual user utterance. Driver explicitly identified two failure cases ("Wann wurde Einstein geboren?" → "Jawohl Sir", "Wann ist Montag?" → "Mache ich") as evidence that few-shot priming defeats the contextual-acknowledgment goal. The revised §4 uses rules + negative examples only, no positive template phrases, and adds an explicit "stay silent" branch for smalltalk and quick factual questions. <!-- i18n-allow: quoted German voice-output examples -->
**Cross-references:** ADR-0011 (Router-Discipline), `jarvis/brain/ack_generator.py` (current template-based implementation), `jarvis/brain/output_filter.py` (40-pattern voice blacklist), `jarvis/missions/voice/announcer.py` (sibling MissionAnnouncer pattern)

---

## 1. Goal & Non-Goals

### Goal

Replace the template-based, per-tool acknowledgment generator with a dedicated, provider-pluggable **Flash-Brain** that runs **in parallel** with the Router-Brain on every user utterance. The Flash-Brain emits a single short, context-aware, butler-style sentence that the user hears within ~500-900 ms of finishing their utterance — before deep reasoning and tool execution start.

The defining property of "this time it works": the spoken acknowledgment must be **specific to what the user said**, not a generic phrase. The user's failure example — "Wann wird Albel eingestellt?" answered with "Verstanden, ich kümmere mich darum." — must be impossible by construction. <!-- i18n-allow: quoted German voice interaction example -->

### Concrete Before / After

```
Today (template generator, ack_generator.py:166-251):
  User:   "Wann wird Albel eingestellt?"  <!-- i18n-allow: quoted German voice-input example -->
  Router: search_web(query="Albel")
  Ack:    "Verstanden, ich kümmere mich darum."      ← generic, wrong tonality  <!-- i18n-allow: quoted German voice-output example -->

Goal (Flash-Brain, parallel):
  User:   "Wann wird Albel eingestellt?"  <!-- i18n-allow: quoted German voice-input example -->
  Flash:  "Hole die Einstellungs-Info zu Albel."     ← topic-specific (names "Albel" + "Einstellung")  <!-- i18n-allow: quoted German voice-output example -->
  Router: search_web(query="Albel Einstellung")  <!-- i18n-allow: quoted German search query example -->
  Final:  "Albel beginnt voraussichtlich im Juli."  <!-- i18n-allow: quoted German voice-output example -->

Counter-example for the same kind of question that should NOT trigger an ack:
  User:   "Wann wurde Albert Einstein geboren?"  <!-- i18n-allow: quoted German voice-input example -->
  Flash:  ""                                          ← silent: factual question with a
                                                        single canonical answer the main
                                                        brain returns in < 1 s. A pre-sentence
                                                        ("Jawohl Sir") would be noise.
```

### Non-Goals

- **Not** introducing a new TTS provider or audio path. The Flash-Brain's output is published on the existing `AnnouncementRequested` bus event and inherits whichever TTS provider the user has configured in `[tts]`.
- **Not** changing the Router-Brain or the existing tool-use loop. The Flash-Brain runs **alongside**, never **inside**.
- **Not** Router-Extended (single LLM call co-emitting preamble). That approach was specced one day prior (2026-05-10) and explicitly rejected by the driver in favor of a separable, swappable Flash-Brain.
- **Not** a subprocess. The Flash-Brain is an in-process Brain-Plugin like all other brain providers.
- **Not** a fallback to generic templates on failure. Silent failure is the only failure mode (see §6).
- **Not** wiring into Phase-6 missions or sub-tier brains. Top-level user utterances only.

---

## 2. User Stories

**US-1 (the Albel case — knowledge questions must not get action-acks):**
> As Alex, when I ask Jarvis a factual question, I want the acknowledgment to match the question shape ("Lass mich kurz nachschauen") and not the tool that the router happens to dispatch behind the scenes. The acknowledgment's tonality must come from my utterance, not from the router's tool choice.

**US-2 (heavy task — the motivating long-latency case):**
> As Alex, when I ask for a long research or build task, I want to hear within ~1 second that Jarvis understood and started, with the topic named if reasonable. The 5-30 s gap until the real answer must feel like deliberate work, not silent confusion.

**US-3 (smalltalk and short interactions):**
> As Alex, when I say "Hallo Jarvis" or "wie geht's", I want a brief, natural acknowledgment from Jarvis — same persona, same voice — without it sounding like a robotic preamble. The Flash-Brain is the only entity that speaks on every turn.

**US-4 (silence is golden on failure):**
> As Alex, when the Flash-Brain hits a timeout, provider error, scrub-empty, or any other failure, I want nothing to be spoken from the ack path. The main brain still answers. A generic "Verstanden, ich kümmere mich darum." across all my requests was exactly the failure mode of the previous template-based attempt; it must never come back. <!-- i18n-allow: quoted German voice-output example -->

**US-5 (provider flexibility):**
> As Alex, when a new fast LLM (Grok-4-Flash, Gemini 3.1 Flash, GPT-5-mini, Groq-Llama) comes out, I want to be able to switch the Flash-Brain to it through configuration alone, without touching the rest of Jarvis. Default should never be on a deprecated model (Gemini 2.5 Flash is out as of today).

**US-6 (TTS-agnostic):**
> As Alex, whichever TTS provider I have configured (Gemini-TTS, Grok-Voice, ElevenLabs, etc.), the Flash-Brain's spoken output must use **that same provider and same voice** — never a hardcoded different one. The user-perceived voice identity must remain constant across ack and main response.

---

## 3. Architecture

### Approach: Parallel In-Process Flash-Brain

Two `asyncio.Task` instances run concurrently for every user utterance:

1. **Flash-Brain** — fast, butler-personality LLM (~200-500 ms) that emits exactly one short sentence based on the user's raw utterance.
2. **Router-Brain** — existing Haiku 4.5 classification + tool selection.

When the Flash-Brain finishes, its output is scrubbed through `scrub_for_voice` and published as `AnnouncementRequested(kind="preamble")`. The existing `_on_announcement` handler in `jarvis/speech/pipeline.py:647` picks it up, sends it through the configured TTS provider, and queues the audio on `player.play_chunks`.

When the Router-Brain finishes, the tool-use-loop executes the chosen tool. When the tool completes and the brain has its final answer, that goes through the existing `_handle_utterance` → `_speak` → `synthesize` path. The `player.play_chunks` queue serializes the two audio streams in arrival order.

### Why "Parallel" structurally solves the Albel problem

The Flash-Brain sees **only the user's utterance** and a static persona prompt. It does **not** see the Router-Brain's tool decision. Therefore it cannot be misled by "search_web was selected, so this must be an action" — it classifies tonality purely from the language of the request itself:

- `"Wann wird X eingestellt?"` → reads as a question → emits `"Lass mich kurz nachschauen."` <!-- i18n-allow: quoted German voice example -->
- `"Mach X auf."` → reads as an action → emits `"Mache ich, X öffnet sich gleich."` <!-- i18n-allow: quoted German voice example -->

This decoupling is the central correctness property.

### Data Flow

```
                User: "Wann wird Albel eingestellt?"  <!-- i18n-allow: quoted German voice-input example -->
                                  │
                                  ▼
                       [STT: faster-whisper]
                                  │
                Final: "Wann wird Albel eingestellt?"  <!-- i18n-allow: quoted German voice-input example -->
                                  │
                                  ▼
                  pipeline emits FinalTranscriptReady on bus
                                  │
                                  ▼
            ┌────────── asyncio.gather ──────────┐
            │                                     │
            ▼                                     ▼
    AckGenerator.run()                    BrainManager.generate()
    (Flash-Brain, e.g. Gemini 3.1)        (Router-Brain, Haiku 4.5)
            │                                     │
    HTTP call to Gemini API               HTTP call to Anthropic API
    persona_prompt + utterance            ROUTER_TOOLS + utterance
    ~200-500 ms                           ~200-500 ms
            │                                     │
            ▼                                     ▼
    text="Lass mich kurz                  tool_call=search_web(
       nachschauen."                         query="Albel ...")
            │                                     │
    scrub_for_voice                       tool_use_loop.execute(...)
    truncate at first [.!?]                       │
    lang sanity-check                       (1-3 s of work)
            │                                     │
    ┌────────────────────┐                        │
    │ if empty: silent   │                        │
    │ if banned: silent  │                        │
    └────────┬───────────┘                        │
             │                                    │
             ▼                                    ▼
       bus.publish(                         bus.publish(
          AnnouncementRequested(               ResponseGenerated(
            text=text,                           text=final_answer))
            kind="preamble",
            priority="normal"))
             │                                    │
             ▼                                    ▼
        _on_announcement                  _on_response  →
        → TTS.synthesize                  → TTS.synthesize
        → player.play_chunks              → player.play_chunks
        (Queue position 1)                (Queue position 2)
             │                                    │
        ~900 ms after STT-end                ~3200 ms after STT-end
        User hears:                         User hears:
        "Lass mich kurz                     "Albel beginnt
         nachschauen."                       voraussichtlich im Juli."
```

### Three Critical Properties

**P1 — Latency-bounded.** `asyncio.wait_for(ack_call, timeout=ack_timeout_ms / 1000)` enforces a hard 1500 ms ceiling. If the Flash-Brain doesn't return by then, the task is cancelled and a `silent` outcome is recorded.

**P2 — Audio order guaranteed by existing queue.** `jarvis/audio/player.py::play_chunks` is single-consumer. The first `synthesize` call (the Flash-Brain output) is queued first; the second (the main response) waits for it to finalize. There is never overlap. Barge-in (`"sei still"`) uses the existing `priority="interrupt"` mechanism and stops both.

**P3 — Blacklist-compliant.** Output passes through `scrub_for_voice` ([`jarvis/brain/output_filter.py`](../../../jarvis/brain/output_filter.py)) before being emitted on the bus. The 40-pattern blacklist blocks "Subagent" / "Sub-Agent" / "Worker" / "Sir" / stack traces / markdown residues. "Jarvis-Agent" is allowed (already used by MissionAnnouncer). (2026-05-24 update: the retired "OpenClaw" brand token is now actively stripped instead — this design predates that change.) If scrub returns less than 3 alphanumeric characters, the ack is silent.

---

## 4. Components

### Backend

```
jarvis/brain/ack_brain/                           [NEW MODULE]
├── __init__.py                  exposes AckGenerator class
├── generator.py                 AckGenerator.run(utterance, language) → str | None
│                                  - dispatches to provider plugin
│                                  - applies asyncio.wait_for timeout
│                                  - calls scrub_for_voice on output
│                                  - truncates at first [.!?] if > 25 words
│                                  - returns None on any failure
├── persona_prompt.py            PERSONA_PROMPT_DE / PERSONA_PROMPT_EN constants
│                                  ~600 chars each, 8 few-shot examples
├── config.py                    Pydantic AckBrainConfig (matches [ack_brain] toml)
├── circuit_breaker.py           Simple state machine: closed / open / half-open
│                                  3 consecutive failures → open for 60s → half-open
└── providers/                   Provider-specific adapters
    ├── __init__.py              registry mapping provider name → adapter class
    ├── base.py                  AbstractAckProvider (Protocol)
    ├── gemini.py                GeminiFlashAck (uses google-genai SDK)
    ├── grok.py                  GrokFlashAck (uses existing Grok HTTP client)
    ├── openai.py                OpenAIMiniAck (uses openai SDK)
    └── ollama.py                OllamaFlashAck (local fallback option)

jarvis/brain/ack_generator.py                     [REFACTOR]
  Keep:
    - ACK_SKIP_TOOLS frozenset (still used to short-circuit ack on
      passive read tools — Flash-Brain skipped for these by router.py)
    - is_voice_control_utterance() (still useful as fast pre-check)
    - final_summary_marker() / should_prepend_marker() (orthogonal feature)
  Remove:
    - All per-tool template handlers (_ack_dispatch_harness, _ack_run_shell,
      _ack_search_web, _ack_spawn_sub_jarvis, _ack_multi_spawn, _ack_open_app,
      _ack_run_skill, _ack_remember, _ack_verify, _ack_start_preview_server,
      _ack_set_config, _TEMPLATES dict, _GENERIC_ACK dict)
  Replace generate_ack signature:
    async def generate_ack(
        utterance: str,
        *,
        language: str = "de",
        ack_brain: AckGenerator | None = None,
    ) -> str | None:
        if ack_brain is None:
            return None  # feature disabled or not wired
        if is_voice_control_utterance(utterance):
            return None  # fast bypass
        return await ack_brain.run(utterance, language=language)

jarvis/brain/router.py                            [MODIFY]
  _build_ack_emitter() now closes over a passed AckGenerator instance
  instead of dispatching to template lookups. The emitter signature
  remains compatible with tool_use_loop.py:254-334 (no breaking change).
  Wiring: BrainManager constructs AckGenerator at startup and threads
  it through to the Router-Brain.

jarvis/brain/factory.py                           [MODIFY]
  build_default_brain() reads [ack_brain] section, instantiates the
  appropriate provider plugin, wires AckGenerator into BrainManager.
  If [ack_brain].enabled = false, AckGenerator is None and the
  existing silent-fallback path takes over.

jarvis/core/config.py                             [MODIFY]
  Add AckBrainConfig Pydantic model (top-level section [ack_brain]).
  Default values:
    enabled = false  (opt-in until verified)
    provider = "gemini"
    timeout_ms = 1500
    on_failure = "silent"  (Literal["silent"] — no other modes for now)
    circuit_breaker_threshold = 3
    circuit_breaker_cooldown_s = 60

  Plus nested provider sub-models (GeminiAckProvider, GrokAckProvider, ...)
  with model name and credential-key references.

jarvis/core/events.py                             [MINOR]
  AnnouncementRequested already exists. Add optional
    kind: Literal["preamble", "completion", "info"] | None = None
  Default None preserves backwards compatibility with MissionAnnouncer.

jarvis/telemetry/                                 [MINOR]
  Add ack_* counter names to the known-counters list, no schema change.
```

### Config (jarvis.toml)

```toml
[ack_brain]
enabled = true
provider = "gemini"
timeout_ms = 1500
on_failure = "silent"
circuit_breaker_threshold = 3
circuit_breaker_cooldown_s = 60

[ack_brain.providers.gemini]
model = "gemini-3.1-flash"
api_key_secret = "gemini_api_key"  # resolves via Windows Credential Manager
temperature = 0.6
max_output_tokens = 40

[ack_brain.providers.grok]
model = "grok-4-flash"
api_key_secret = "grok_api_key"
temperature = 0.6
max_output_tokens = 40

[ack_brain.providers.openai]
model = "gpt-5-mini"
api_key_secret = "openai_api_key"
temperature = 0.6
max_output_tokens = 40

[ack_brain.providers.ollama]
model = "llama3.1:8b"
endpoint = "http://localhost:11434"
temperature = 0.6
max_output_tokens = 40
```

The Setup-Wizard ([`jarvis/setup/wizard.py`](../../../jarvis/setup/wizard.py)) gets a new step (German wizard prompt shown to the user, "Which fast LLM provider should Jarvis use for the short acknowledgment sentences you hear before the main answer?"):
> "Welchen schnellen LLM-Provider soll Jarvis für die kurzen Bestätigungssätze verwenden, die du vor der Hauptantwort hörst?" <!-- i18n-allow: quoted German wizard UI prompt -->

Options listed dynamically based on which API keys the user already has in the Credential Manager.

### Persona-Prompt (revised 2026-05-13, locked in this spec)

Both languages mirror each other in shape and intent. Key invariants:

- EXACTLY ONE sentence, max 12 words, OR an empty string.
- Two-branch decision: the prompt MUST first decide whether to speak at all. If the request is smalltalk, a quick factual question, or voice control, the LLM returns an empty string. Only longer-running tasks (research, multi-step action, external service, data lookup) get a sentence.
- The sentence, when emitted, MUST reference the concrete topic of the request (search subject, app name, data object, location, person, …). Generic acknowledgments like "Mache ich" / "On it" / "Verstanden" / "Sure thing" are explicitly forbidden because they pass any utterance equally well — the failure mode the previous template generator and the original few-shot prompt both produced.
- Forbidden (defense-in-depth alongside `scrub_for_voice`): Subagent / Sub-Agent / Worker / Provider (alone) / Sir / Sehr wohl / Jawohl / Boss / Chef (as address).
- Allowed: Jarvis-Agent / Jarvis / brand names (Spotify, Discord, GitHub, Outlook, …) / topical nouns (calendar, meeting, flights, weather, …).
- NO positive few-shot examples. The previous spec embedded eight per language and the LLM reproduced them verbatim regardless of fit. The revised prompt uses negative examples ("never say …") and structural rules only.

Committed verbatim as `PERSONA_PROMPT_DE` and `PERSONA_PROMPT_EN` constants in `persona_prompt.py`. No template strings, no string interpolation — the prompt is fully static.

#### PERSONA_PROMPT_DE (verbatim)

```text
Du bist JARVIS, der persönliche Assistent des Nutzers. Du bist gerade in  <!-- i18n-allow: PERSONA_PROMPT_DE -->
deiner "Vor-Antwort"-Rolle: kurz und kontextbezogen sprechen, BEVOR die  <!-- i18n-allow: PERSONA_PROMPT_DE -->
eigentliche Antwort fertig ist — aber nur dann, wenn es dem Nutzer wirklich  <!-- i18n-allow: PERSONA_PROMPT_DE -->
hilft. Lieber schweigen als kontextlos plappern.

KRITISCH — du beantwortest die Frage NIEMALS inhaltlich:
Du bist NICHT das Hauptmodell. Ein anderes, größeres Modell beantwortet  <!-- i18n-allow: PERSONA_PROMPT_DE -->
die Frage direkt nach dir, oft in weniger als einer Sekunde. Deine  <!-- i18n-allow: PERSONA_PROMPT_DE -->
einzige Aufgabe ist ein kurzer Vor-Satz ODER Schweigen. Wenn du die  <!-- i18n-allow: PERSONA_PROMPT_DE -->
Frage selbst beantwortest (mit Fakten, Datum, Name, Definition,
Erklärung), hört der User die Antwort doppelt — einmal von dir, einmal  <!-- i18n-allow: PERSONA_PROMPT_DE -->
vom Hauptmodell. Das ist IMMER falsch. Beispiele für verbotene  <!-- i18n-allow: PERSONA_PROMPT_DE -->
Eigen-Antworten:
- "Albert Einstein wurde am 14. März 1879 geboren." → FALSCH, schweige.  <!-- i18n-allow: PERSONA_PROMPT_DE -->
- "Die Hauptstadt von Italien ist Rom." → FALSCH, schweige.  <!-- i18n-allow: PERSONA_PROMPT_DE -->
- "Albel wird am 15. Oktober eingestellt." → FALSCH (Halluzination),  <!-- i18n-allow: PERSONA_PROMPT_DE -->
  schweige oder beschreibe nur die Suche.  <!-- i18n-allow: PERSONA_PROMPT_DE -->

OBERSTE REGEL — keine generischen Floskeln:  <!-- i18n-allow: PERSONA_PROMPT_DE -->
Verboten sind Bestätigungen ohne konkreten Bezug zur Anfrage:  <!-- i18n-allow: PERSONA_PROMPT_DE -->
- "Mache ich" / "Klar" / "Verstanden" / "Ich kümmere mich darum"  <!-- i18n-allow: PERSONA_PROMPT_DE -->
- "Jawohl" / "Sehr wohl" / "Sir" / "Chef" / "Boss" als Anrede  <!-- i18n-allow: PERSONA_PROMPT_DE -->
- "Lass mich kurz nachschauen" / "Ich überlege" als reine Floskel  <!-- i18n-allow: PERSONA_PROMPT_DE -->
- Jede Phrase, die auf jede beliebige andere Anfrage genauso passen würde  <!-- i18n-allow: PERSONA_PROMPT_DE -->

WANN DU SPRICHST (ein einziger Satz, max 12 Wörter):  <!-- i18n-allow: PERSONA_PROMPT_DE -->
- Wenn die Anfrage offensichtlich eine längere Aufgabe auslöst:  <!-- i18n-allow: PERSONA_PROMPT_DE -->
  Recherche, mehrstufige Aktion, externer Dienst, Datenabfrage.
- Dein Satz muss das KONKRETE Thema der Anfrage erwähnen  <!-- i18n-allow: PERSONA_PROMPT_DE -->
  (Suchgegenstand, App-Name, Datenobjekt, Ort, Person). KEINE  <!-- i18n-allow: PERSONA_PROMPT_DE -->
  memorierten Standardphrasen — jeder Satz wird neu formuliert für  <!-- i18n-allow: PERSONA_PROMPT_DE -->
  genau diese Anfrage.  <!-- i18n-allow: PERSONA_PROMPT_DE -->

WANN DU SCHWEIGST (Output: leerer String ""):
- Smalltalk ("Hallo", "Wie geht's", "Hey", "Danke").
- Schnelle Faktenfragen ("Wann wurde Einstein geboren?", "Wieviel Uhr  <!-- i18n-allow: PERSONA_PROMPT_DE -->
  ist es?", "Hauptstadt von Italien?"). Das Hauptmodell antwortet  <!-- i18n-allow: PERSONA_PROMPT_DE -->
  direkt — ein Vor-Satz würde nur stören.  <!-- i18n-allow: PERSONA_PROMPT_DE -->
- Voice-Control ("Sei still", "Stopp", "Pause").
- Wenn du unsicher bist, ob ein Vor-Satz hier passt: schweigen.  <!-- i18n-allow: PERSONA_PROMPT_DE -->

VERBOTENES VOKABULAR (auch in erlaubten Sätzen, defense-in-depth):  <!-- i18n-allow: PERSONA_PROMPT_DE -->
"Subagent", "Sub-Agent", "Worker", "Provider" (alleinstehend),
"Sir", "Sehr wohl", "Jawohl", "Boss".

ERLAUBT in deinem Satz:
"Jarvis-Agent", "Jarvis", Marken-Namen (Spotify, Discord, GitHub, Outlook,
…), sachliche Themen-Wörter (Kalender, Termin, Flüge, Wetter, …).  <!-- i18n-allow: PERSONA_PROMPT_DE -->

Output: Genau ein Satz mit konkretem Themenbezug, ODER leerer String.  <!-- i18n-allow: PERSONA_PROMPT_DE -->
Kein Markdown, kein Kommentar, kein Begleitsatz.  <!-- i18n-allow: PERSONA_PROMPT_DE -->
``` <!-- i18n-allow: PERSONA_PROMPT_DE -->
(Note: `persona_prompt.py` has since been substantially revised — the current
prompt forbids saying "Jarvis-Agent" outright rather than allowing it. This
verbatim quote reflects the 2026-05-11 version this design shipped.)

#### PERSONA_PROMPT_EN (verbatim)

```text
You are JARVIS, the user's personal assistant. You are in your
"pre-answer" role: speak briefly and context-specifically BEFORE the
actual answer is ready — but only when it genuinely helps the user.
Silence beats context-free filler.

CRITICAL — you NEVER answer the question on substance:
You are NOT the main model. A separate, larger model answers the
question directly after you, usually in under a second. Your only job
is a brief pre-sentence OR silence. If you answer the question yourself
(with facts, dates, names, definitions, explanations), the user hears
the answer twice — once from you, once from the main model. That is
ALWAYS wrong. Examples of forbidden self-answers:
- "Albert Einstein was born on March 14, 1879." → WRONG, stay silent.
- "The capital of Italy is Rome." → WRONG, stay silent.
- "Albel starts on October 15th." → WRONG (hallucination); stay silent
  or describe only the lookup.

TOP RULE — no generic filler:
Forbidden are acknowledgments without concrete reference to the
request:
- "On it" / "Got it" / "Sure" / "Understood" / "I'll handle that"
- "Sir" / "Boss" / "Chief" as honorifics
- "Let me check on that" / "Let me think" as pure filler
- Any phrase that would fit any other request equally well

WHEN YOU SPEAK (single sentence, max 12 words):
- When the request clearly triggers a longer task: research,
  multi-step action, external service, data lookup.
- Your sentence MUST mention the CONCRETE topic of the request
  (search subject, app name, data object, location, person). No
  memorised standard phrases — every sentence is freshly formulated
  for this exact request.

WHEN YOU STAY SILENT (output: empty string ""):
- Smalltalk ("Hi", "How are you", "Hey", "Thanks").
- Quick factual questions ("When was Einstein born?", "What time is
  it?", "Capital of Italy?"). The main model answers directly — a
  pre-sentence would only disrupt.
- Voice control ("Be quiet", "Stop", "Pause").
- When unsure whether a pre-sentence fits here: stay silent.

FORBIDDEN VOCABULARY (also inside allowed sentences,
defense-in-depth):
"Subagent", "Sub-Agent", "Worker", "Provider" (standalone), "Sir",
"Very well", "Boss", "Chief".

ALLOWED in your sentence:
"Jarvis-Agent", "Jarvis", brand names (Spotify, Discord, GitHub,
Outlook, …), topical nouns (calendar, meeting, flights, weather, …).

Output: Exactly one sentence with concrete topical reference, OR an
empty string. No markdown, no comments, no accompanying text.
```
(Note: `persona_prompt.py` has since been substantially revised — the current
prompt forbids saying "Jarvis-Agent" outright rather than allowing it. This
verbatim quote reflects the 2026-05-11 version this design shipped.)

#### Empirical validation (2026-05-13)

A 10-utterance smoke battery against the v2.1 prompt (Grok-4-Fast-Non-Reasoning standing in for the unavailable Gemini-Flash; user's Google AI Studio project is currently 403) achieved 7/10 PASS:

| Pattern | Cases | v2.1 outcome |
|---|---|---|
| Smalltalk → silent | "Wie geht's dir?" | PASS (`""`) |
| Smalltalk → silent | "Hallo Jarvis" | FAIL — got `"Hallo Alex."` (greeting echo) |
| Quick factual → silent | Einstein, Italy capital | PASS for both (`""`) |
| Quick factual → silent | "Wann ist Montag?" | FAIL — got next-Monday date |
| Voice control → silent | "Sei still" | PASS (`""`) |
| Action → topic-aware | Spotify, TTS-voice, Flüge | PASS (all reference the concrete topic) | <!-- i18n-allow: quoted German test-fixture noun -->
| External lookup → topic-aware | Albel | FAIL — got `""` (over-silent after disclaimer) |

Pure prompt engineering plateaus around 70% on this battery. The three residual failure shapes (greeting echo, factual self-answer, over-silence on lookup) are all detectable post-hoc by the F10 heuristic in §6. Combined with the prompt, the dual-layer design SHOULD reach 10/10 on this exact battery and is the binding architecture for E3.

The smoke battery (`scripts/_test_persona_prompt_empirie.py`) is the standing harness for re-validation after prompt or post-filter changes.

### UI / Frontend

```
jarvis/ui/web/server.py                          [SMALL]
  AnnouncementRequested events with kind=="preamble" are forwarded to
  the chat WebSocket as a new message role.

jarvis/ui/web/frontend/src/types/messages.ts     [SMALL]
  type MessageRole = "user" | "jarvis" | "preamble"

jarvis/ui/web/frontend/src/views/ChatView.tsx    [MODIFY]
  Render preamble bubbles with muted styling + small "pre-ack" chip
  so the history shows what was acknowledged vs what was the final answer.
```

### Tests

```
tests/unit/brain/test_ack_generator.py                  [REWRITE, ~10 cases]
tests/unit/brain/test_ack_brain/                        [NEW DIR]
├── test_generator.py                                   ~12 cases
├── test_persona_prompt.py                              ~6 cases (substring + length)
├── test_circuit_breaker.py                             ~5 cases
└── providers/
    ├── test_gemini.py                                  ~4 cases (fakes only)
    ├── test_grok.py                                    ~4 cases
    └── test_openai.py                                  ~4 cases
tests/integration/test_ack_flow.py                      [NEW, ~6 cases]
tests/integration/test_ack_provider_swap.py             [NEW, ~3 cases]
tests/contract/test_ack_provider_protocol.py            [NEW, ~3 cases]
scripts/smoke-test-ack.ps1                              [NEW, manual]
```

---

## 5. Behavior Matrix

Two columns matter: whether the Flash-Brain produces text at all (silent vs speaks) and, when it speaks, whether the text references the concrete topic. Generic outputs are not acceptable for any non-empty row.

| User Input                            | Expected Flash-Brain Output            | Reason                                                        | Spoken Order                                |
|---------------------------------------|----------------------------------------|---------------------------------------------------------------|---------------------------------------------|
| `"Mach Spotify auf"`                  | Topic-specific sentence mentioning "Spotify" (LLM-generated, e.g. "Spotify öffne ich grad.") | Action with visible execution lag | Ack → app launch → Main response            | <!-- i18n-allow: quoted German voice example -->
| `"Such mir SF-Flüge für morgen"`      | Topic-specific sentence mentioning "Flüge" and/or "San Francisco" | Multi-step external task             | Ack → spawn_worker → Final answer           | <!-- i18n-allow: quoted German voice example -->
| `"Wann wird Albel eingestellt?"`      | Topic-specific sentence referencing "Albel" or "Einstellung" | External lookup needed                       | Ack → search_web → Main answer              | <!-- i18n-allow: quoted German voice example -->
| `"Wann wurde Einstein geboren?"`      | `""` (silent)                          | Quick factual question, main brain answers directly in < 1 s  | Main response only                          | <!-- i18n-allow: quoted German voice example -->
| `"Was ist die Hauptstadt von Italien?"`| `""` (silent)                         | Same — main brain trivia                                      | Main response only                          | <!-- i18n-allow: quoted German voice example -->
| `"Wann ist Montag?"`                  | `""` (silent)                          | Trivial date question, main brain answers directly            | Main response only                          |
| `"Hallo Jarvis"`                      | `""` (silent)                          | Pure smalltalk, main brain greets back                        | Main response only                          |
| `"Wie geht's dir?"`                   | `""` (silent)                          | Pure smalltalk                                                | Main response only                          |
| `"Sei still"`                         | `""` (silent)                          | Voice control                                                 | Voice-control bypass triggers audio stop    |
| `"Was sollte ich essen?"`             | `""` (silent)                          | Reflection, no external lookup, main brain answers in < 2 s   | Main response only                          | <!-- i18n-allow: quoted German voice example -->
| `"Ändere TTS-Stimme auf Lara"`        | Topic-specific sentence referencing "TTS-Stimme" or "Lara" | Action with config-mutation latency             | Ack → set_config_value → Echo confirm       | <!-- i18n-allow: quoted German voice example -->
| LLM emits `"Mache ich"` / `"On it"`   | scrubbed → empty (generic filler is in the forbidden list at runtime) | Counter-example: prompt forbids it, post-check enforces | Silent, `ack_scrubbed_empty_total` increments |
| LLM emits a generic 40-word ramble    | Truncated at first `[.!?]`; if still generic → scrubbed empty | Truncation handles length, scrub handles content    | Either short topical ack or silent          |
| LLM emits `"Sub-Agent läuft"`         | scrub strips → empty                   | Blacklist                                                     | Silent, `ack_scrubbed_empty_total` increments| <!-- i18n-allow: quoted German voice example -->

---

## 6. Failure Handling

| ID | Scenario                                       | Behavior                                              | Telemetry counter                          |
|----|------------------------------------------------|-------------------------------------------------------|--------------------------------------------|
| F1 | Provider API > 1500 ms                         | `asyncio.wait_for` cancels, returns None              | `ack_timeout_total{provider}`              |
| F2 | Provider API HTTP 5xx                          | Caught, log warning, return None — no retry           | `ack_provider_error_total{provider, status}`|
| F3 | LLM returns empty / whitespace                 | Return None                                           | `ack_empty_response_total{provider}`       |
| F4 | LLM returns > 25 words                         | Truncate at first `[.!?]`, keep first sentence        | `ack_truncated_total`                      |
| F5 | LLM uses banned vocabulary                     | `scrub_for_voice` strips, post-scrub empty → None     | `ack_scrubbed_empty_total`                 |
| F6 | LLM returns wrong language for detected utterance | Heuristic check (top-100 word list per lang); silent | `ack_lang_mismatch_total`                  |
| F7 | TTS synthesis fails on the ack text            | Existing TTS error path; main response unaffected     | `ack_tts_failed_total`                     |
| F8 | 3 consecutive F1/F2 from same provider         | Circuit breaker opens, 60 s cooldown, then half-open  | `ack_circuit_breaker_open_total{provider}` |
| F9 | `[ack_brain].enabled = false`                  | AckGenerator never instantiated, no bus events        | (static off, no counter)                   |
| F10| LLM emits a substantive answer instead of a pre-sentence (e.g. `"Rom."`, `"Albert Einstein wurde am 14. März 1879 geboren."`, `"Der nächste Montag ist am 9. September 2024."`) | Heuristic post-filter in `AckGenerator.run()` detects answer-shaped outputs and suppresses; return None | `ack_self_answer_suppressed_total{pattern}` | <!-- i18n-allow: quoted German voice example -->

**Design principle: Silent or Strong.** No failure mode falls back to a generic template. The previous template-based implementation's defining failure was "Verstanden, ich kümmere mich darum." emitted indiscriminately; reintroducing that on errors would defeat the spec. <!-- i18n-allow: quoted German voice-output example -->

**F10 detection heuristic (binding for E3 AckGenerator):**

Empirical validation on 2026-05-13 with Grok-4-Fast-Non-Reasoning + v2.1 prompts achieved 7/10 PASS on a 10-utterance battery covering smalltalk / factual / action / research / voice-control / config-mutation. The 3 residual failures all share one shape: the LLM was asked a factual question and answered it directly (Einstein's birthdate / Italy's capital / next Monday's date) instead of staying silent. Pure prompt engineering plateaus here; the cleanest fix is a second-line defence in the AckGenerator that detects answer-shaped outputs and silently suppresses them.

The post-filter SHOULD treat the following patterns as answer-shaped and suppress:

- **Date answer**: the output contains a date / time / day-of-week token (`\b\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4}\b`, `\b\d{1,2}:\d{2}\b`, month names, `Montag`/`Dienstag`/…/`Monday`/…) AND is NOT preceded by an action verb (`suche`, `prüfe`, `hole`, `look up`, `check`, …). <!-- i18n-allow: German input vocabulary -->
- **Single-word fact**: the output is one or two words ending in `.`, with the words being noun-shaped and not a topic-reference (e.g. `"Rom."`, `"Berlin."`, `"42."`).
- **"X is Y" definition**: the output matches `(.*)\s(ist|sind|war|waren|is|are|was|were)\s(.*)\.` AND lacks any action verb of the SPEAK-branch (recherchiere / suche / hole / schaue / starte / öffne / wechsle / look up / search / fetch / launch / change). <!-- i18n-allow: German input vocabulary -->

The post-filter MUST NOT touch:

- Topic-aware action descriptions like `"Suche Flüge nach München für morgen."` — matches the "X is Y" pattern superficially but starts with an action verb. <!-- i18n-allow: quoted German example -->
- Empty output (already silent).
- Output already scrubbed by `scrub_for_voice`.

Empirical re-run after the post-filter ships SHOULD push pass-rate to 10/10 on the same battery. The smoke-test script (`scripts/_test_persona_prompt_empirie.py`) is the standing harness for this re-validation.

Counter `ack_self_answer_suppressed_total{pattern}` MUST tag the matching pattern (`date_answer` / `single_word_fact` / `definition`) so operational dashboards can show whether the post-filter is rescuing real failure modes or shadow-rejecting valid acks.

---

## 7. Telemetry

Counters published via the existing flight-recorder bus:

```
ack_called_total{provider}             — Flash-Brain was invoked
ack_emitted_total{provider}            — Output was scrubbed, bus-published, TTS-queued
ack_timeout_total{provider}            — F1
ack_provider_error_total{provider, status} — F2
ack_empty_response_total{provider}     — F3
ack_truncated_total                    — F4
ack_scrubbed_empty_total               — F5
ack_lang_mismatch_total                — F6
ack_tts_failed_total                   — F7
ack_circuit_breaker_open_total{provider} — F8
ack_self_answer_suppressed_total{pattern} — F10 (pattern ∈ {date_answer, single_word_fact, definition})
ack_latency_ms_histogram{provider}     — wall-clock from call start to bus-publish
```

### Operational health ratios

- `ack_emitted_total / ack_called_total` — primary health
  - `> 0.85` healthy
  - `0.60 – 0.85` warning, surface in `--check` and `--plugins`
  - `< 0.60` alarm, Setup-Wizard offers provider swap on next start

- `p95(ack_latency_ms_histogram) < 1200 ms` — latency SLA
  - Above 1200: log warning, suggest faster provider

---

## 8. Test Strategy

### Unit — `tests/unit/brain/test_ack_brain/`

**`test_generator.py` (~12 cases)**
- `run()` returns provider output on happy path
- `run()` returns None when timeout exceeded (use `asyncio.sleep` fake)
- `run()` returns None when provider raises arbitrary exception
- `run()` returns None when provider returns empty string
- `run()` returns None when provider returns only whitespace
- `run()` truncates output at first `[.!?]` when over 25 words
- `run()` calls scrub_for_voice and returns None if scrub-empty
- `run()` calls scrub_for_voice and returns scrubbed text otherwise
- `run()` increments correct telemetry counter on each failure path
- `run()` records latency in histogram on success
- `is_voice_control_utterance` short-circuits before any LLM call
- Language detection picks PERSONA_PROMPT_DE vs _EN correctly

**`test_persona_prompt.py` (~6 cases)**
- PERSONA_PROMPT_DE contains "Mache ich" substring (action template)
- PERSONA_PROMPT_DE contains "nachschauen" substring (question template)
- PERSONA_PROMPT_DE contains "VERBOTEN" instruction block
- PERSONA_PROMPT_DE total length < 1000 chars (latency hygiene)
- PERSONA_PROMPT_EN mirrors DE structure (parametric assertion)
- Both prompts forbid the exact tokens "Subagent", "Sir", "Sehr wohl"

**`test_circuit_breaker.py` (~5 cases)**
- Closed → closed after 1 failure
- Closed → open after threshold failures
- Open ignores calls during cooldown
- Open → half-open after cooldown
- Half-open: 1 success → closed; 1 failure → open

**`providers/test_gemini.py` etc. (~4 cases each)**
Pure fakes, no real API calls. Tests construct response stubs and assert
adapter shapes them into Brain-Protocol-compatible output.

### Integration — `tests/integration/`

**`test_ack_flow.py` (~6 cases)**
- Happy path: utterance → both tasks fire → ack on bus → TTS called once
- Voice-control bypass: `"sei still"` short-circuits, no Flash-Brain call
- Failure: provider error → no AnnouncementRequested → main response still emits
- Audio order: ack timestamp strictly before main response timestamp
- Skip-list: passive read tools (awareness_snapshot) do not trigger Flash-Brain
- Concurrent: Flash-Brain task and Router-Brain task launched same event loop tick

**`test_ack_provider_swap.py` (~3 cases)**
- Config change from gemini → grok → next utterance uses Grok provider
- Provider with missing API key surfaces clear error in `--check`
- Disabled provider via `enabled=false` produces no bus events

### Contract — `tests/contract/test_ack_provider_protocol.py` (~3 cases)
- All registered providers implement the AbstractAckProvider Protocol
- All providers handle empty utterance gracefully (return empty / None)
- All providers respect max_output_tokens config (output truncated at boundary)

### Smoke — `scripts/smoke-test-ack.ps1`

Five scripted utterances per language replayed via simulated STT-final, audio recorded, manual playback verifies:

1. Ack + final are temporally separated, no overlap
2. Ack matches utterance tonality (action vs question vs smalltalk)
3. No "Subagent" / "Worker" / "Sir" / "Sehr wohl" anywhere in audio
4. Voice-control utterance produces zero ack audio
5. Same TTS voice across ack and final response

---

## 9. Open Questions / Future Work

- **Q1.** Should Flash-Brain optionally see the most recent 2-3 conversation turns for continuity? (Approach B from the brainstorm — "Context-Enriched Flash". Deferred: ship Approach A first, add context-enrichment only if isolation feels unnatural in practice.)
- **Q2.** Should the Flash-Brain ALSO act as the trivial-tier-brain for pure smalltalk (i.e., the main brain doesn't respond at all on "Hallo")? Today both will speak. If duplicate-feel is real after testing, add a suppression rule: "if utterance classified as smalltalk and Flash-Brain emitted a complete-sentence ack, suppress main response."
- **Q3.** Persona rotation: today the prompt says "Chef in ~1 of 3 sentences". After two weeks of usage, sample actual distribution; if too butler-heavy or too casual, tune the prompt or add a temperature schedule.
- **Q4.** Should we capture the Flash-Brain output in `data/jarvis_desktop.log` separately from the main brain output for easier debugging?

---

## 10. Cross-References

- **ADR-0011 (Router-Discipline)** — `docs/adr/0011-router-pure-dispatcher.md`. The Flash-Brain is **outside** the Router's responsibility. The Router stays a pure dispatcher; the Flash-Brain handles persona narration. Clean separation of concerns.
- **`jarvis/brain/output_filter.py:scrub_for_voice`** — 40-case blacklist applies to every Flash-Brain output. "Jarvis-Agent" is allowed; "Subagent" / "Worker" / "Sir" / "Sehr wohl" are blocked. (2026-05-24 update: the retired "OpenClaw" brand token is now actively stripped instead — this design predates that change.)
- **`jarvis/missions/voice/announcer.py`** — MissionAnnouncer is the sibling pattern. It also publishes `AnnouncementRequested` events; the Flash-Brain reuses the same bus event + handler. Two producers, one consumer.

---

## 11. Amendment 2026-06-29 — name-neutral persona, em-dash removal, Spanish

Part of the voice-response-behaviour rework (plan: *Jarvis Voice Response
Behaviour: a single, natural, editable system prompt*). The persona constants in
`persona_prompt.py` and `spawn_announcement.py` are amended; this section is the
authoritative record so the file-vs-spec invariant in `persona_prompt.py`'s
module docstring still holds.

1. **Name-neutral.** The opener "Du bist JARVIS" / "You are JARVIS" is replaced
   by "Du bist der persönliche Assistent des Nutzers" / "You are the user's <!-- i18n-allow: quoted German persona-prompt example -->
   personal assistant". The assistant's own name is runtime-derived from the wake
   word (`assistant_name.py`) and owned by the deep brain; the Flash-Brain
   preamble no longer bakes in a product name. The `ALLOWED` list's literal
   "Jarvis" becomes "dein eigener Name" / "your own name". This aligns the
   Flash-Brain with the project-wide name-neutrality change (the persona files
   `SOUL.md` / `JARVIS_PERSONA.md` are also name-neutral as of this date).

2. **Em dashes removed.** Every "—" in the persona constants is replaced by a
   comma or a full stop, matching the new spoken-output rule (an em dash renders
   as a hard TTS pause). `scrub_for_voice` also strips stray em dashes as a
   backstop.

3. **Spanish preamble added.** `PERSONA_PROMPT_ES` is added to `persona_prompt.py`
   and `_normalise_language` / `get_persona_prompt` now resolve `es` natively,
   closing the documented de/en-only gap for the pre-thinking preamble. The
   **spawn-announcement** persona stays de/en (its `es` path is still served by
   the curated deterministic fallback pool, unchanged); a native
   `SPAWN_PERSONA_ES` remains a tracked follow-up.

4. **Unchanged.** The functional contract is untouched: never answer on
   substance, the forbidden-vocabulary and forbidden-action-promise lists
   (verbatim, asserted by `tests/unit/brain/test_routing.py`), the "stay silent"
   branches, and the one-sentence / max-12-words ceiling.
- **Previous spec — `docs/superpowers/specs/2026-05-10-context-rich-preamble-design.md`** — Router-Extended approach, superseded by this one at driver's explicit request. Retained for design-history context.
- **BUG-006 / BUG-014** — When deploying, remember the four-layer restore trap (`docs/BUGS.md`). The editable install can pin to a stale clone, in which case the feature ships in code but not at runtime. Verify with `python -c "import jarvis; print(jarvis.__file__)"` after install.
