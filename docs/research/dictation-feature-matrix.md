# Dictation Feature Matrix — established tools vs. our current state

**Date:** 2026-07-28 · **Status:** research snapshot taken BEFORE the feature
was built — the "our state" column is the starting point, not today's state.

> **Dictation mode shipped the same day.** The rows below marked *partial* or
> *missing* were the work list; they are now implemented (`jarvis/dictation/`,
> the `dictate` keybind, `/api/dictation/*`, the Dictation view, the bar's
> `dictate` mode). This document is kept as-is because it is the evidence the
> design rests on — what the established tools actually do, and where our own
> gaps were. For what was built and why, read the module docstrings under
> `jarvis/dictation/`.

A "dictation mode" (hold a key, speak, release, the text lands in whatever text
field currently has focus) is not a new subsystem for Personal Jarvis. It is a
second entry point into machinery that already exists: a global-hotkey layer
with press/release edges, a transcribe-only dictation session, the STT provider
chain, the clipboard/actuation primitives, and the Jarvis Bar. This document
establishes, feature by feature, **what we already have, what we half-have, and
what is genuinely missing** — and for every missing item, the exact file *and*
function where it would attach.

Every "we already have this" claim below was verified against the code in this
repository on the date above, not from memory. Every platform limitation is
either backed by a source or by a probe run in this repository.

---

## 1. Licence boundary (binding)

This repository is MIT-licensed and public.

* **MIT-licensed projects** (Handy, OpenWhispr, and the wider `awesome-voice-typing`
  list) were read as source. Any pattern adopted from them is re-implemented
  from the described behaviour in our own code; where a specific implementation
  detail is genuinely borrowed, the origin is named in the code comment.
* **GPL-licensed projects** (VoiceInk, nerd-dictation, Vocalinux, Elograf) are
  **behaviour references only**. No code is copied, translated, or derived from
  them. They appear here for what a user can *do*, never for how it is written.
* **Commercial products** are behaviour references observed from their public
  documentation. They are referred to as *commercial reference A* and
  *commercial reference B* rather than by name, for trademark reasons; naming a
  commercial competitor as this project's design template in a public repository
  is a liability we do not need to take on. This is a documentation convention,
  not a claim that the products are secret.

---

## 2. Sources read

| Source | Licence | Platforms | What was read |
|---|---|---|---|
| **Handy** (`cjpais/Handy`) | MIT | Win / macOS / Linux | `src-tauri/src/clipboard.rs`, `src-tauri/src/input.rs`, `src-tauri/src/shortcut/handler.rs`, `src-tauri/src/llm_client.rs`, README |
| **OpenWhispr** (`OpenWhispr/openwhispr`) | MIT | Win / macOS / Linux | README / feature documentation |
| **VoiceInk** (`Beingpax/VoiceInk`) | GPL-3.0 | macOS | README / feature documentation — **behaviour reference only** |
| **awesome-voice-typing** (`primaprashant/…`) | — | all | full list, 33 projects |
| **Commercial reference A** | proprietary | macOS / Win / mobile | public feature documentation |
| **Commercial reference B** | proprietary | macOS / Win / iOS | public feature documentation |

Notable entries from the wider list that changed the analysis:

* **Ghost Pepper** (MIT, macOS) — hold-to-talk with a *local* LLM pass whose
  only job is filler-word cleanup. Proof that the cleanup step is worth
  separating from the recognition step.
* **FnKey** (MIT, macOS, Rust) — microphone is open only while the Fn/Globe key
  is physically held. The most minimal possible push-to-hold contract.
* **Amical** (MIT, macOS/Win) — "context-aware dictation that adapts formatting
  to the app you are using", i.e. the same idea as commercial reference B's
  modes, in an MIT project.
* **nerd-dictation** (GPL, Linux) — types via simulated keystrokes rather than
  the clipboard; the counter-example to the clipboard consensus.
* **VoxType** (MIT, Linux) — explicitly Wayland-optimised; confirms that the
  Wayland story needs its own answer rather than a footnote.
* **whisper-writer**, **hyprwhspr**, **Voquill**, **VOXD** — all converge on the
  same shape (global shortcut → record → transcribe → paste), which is why the
  matrix below treats that shape as the baseline rather than as a design choice.

---

## 3. Feature matrix

Legend: **have** = works today · **partial** = exists but incomplete/disabled,
with the location · **missing** = must be built, with the anchor point.

### 3.1 Trigger

| Feature | Reference tools | Our state | Evidence / anchor |
|---|---|---|---|
| Global shortcut delivering **both** key edges | all | **have** | `jarvis/trigger/hotkey.py::HotkeyTrigger.__init__(push_to_talk=…)` emits `<name>_press` / `<name>_release`; `_build_bindings` wires both handlers |
| Push-to-hold mode | Handy, FnKey, Ghost Pepper, both commercial | **partial** | The recording side is complete (`jarvis/speech/pipeline.py::_ptt_session`, `_on_ptt_press`, `_on_ptt_release`) but **no binding is ever armed**: `jarvis/core/config.py::TriggerConfig.resolve_hotkeys` returns an empty PTT slot and `TriggerConfig.push_to_talk` is documented as "deprecated compatibility field … intentionally ignored" |
| Toggle mode | Handy, OpenWhispr | **have** (as the voice call key) | `pipeline._hotkey_loop` handles the non-PTT `call` event on the release edge only |
| Windows global shortcut | all | **have** | `jarvis/trigger/backends/global_hotkeys.py::GlobalHotkeysBackend` — the `global-hotkeys` package, polling-based; a module-level refcount runs exactly one checker thread |
| macOS global shortcut | all | **have** | `jarvis/trigger/backends/quartz.py::QuartzHotkeyBackend` — listen-only `CGEventTap` on its own CFRunLoop thread, chord matching by physical keycode (deliberately TSM-free, BUG-077) |
| Linux X11 global shortcut | Handy, whisper-writer, nerd-dictation | **partial — backend present, dependency absent** | `jarvis/trigger/backends/pynput.py::PynputBackend` is complete, but `pynput` is declared only for `sys_platform == 'darwin'` in `pyproject.toml`. `jarvis/platform/probes.py::has_hotkey` therefore returns `False` on Linux and `make_hotkey_backend()` selects `NoopBackend`. **Linux has no working global hotkey today, X11 included.** |
| Wayland global shortcut | Handy (via DE config), VoxType | **missing by design** | `jarvis/trigger/backends/noop.py::NoopBackend` — logged once, never raises. See §5 for what the portal route would cost |
| Change the shortcut without restarting | Handy, OpenWhispr, both commercial | **have** | `HotkeyTrigger.rearm` + `pipeline._hotkey_reload_loop` + `PUT /api/settings/keybinds` (`jarvis/ui/web/settings_routes.py::put_keybind`) |
| Shortcut validation / conflict refusal | Handy (partial) | **have, but Windows-biased** | `jarvis/trigger/hotkey.py::validate_hotkey` rejects modifier-only combos, bare letters, Windows-key combos, Alt+F4 and Ctrl+C, and `put_keybind` additionally rejects any subset/superset overlap between actions. But `cmd` is missing from `_MODIFIER_TOKENS`, so the macOS-critical combos pass: calling the function directly on 2026-07-28 returns ACCEPT for `cmd+c`, `cmd+q`, `cmd+w` and `cmd+space`. `f12` is also accepted despite being permanently reserved for the debugger |
| A **separate** dictation shortcut (distinct from the voice-call key) | all | **missing** | Anchor: `jarvis/core/config.py::TriggerConfig` (new field), `jarvis/core/config_writer.py::KEYBIND_ACTIONS` / `KEYBIND_TOML_KEY`, the `hotkey_bindings` dict in `pipeline.run`, and the dispatch in `pipeline._hotkey_loop` |
| Trigger from the on-screen bar instead of a key | commercial A/B, VoiceInk | **partial — dead code** | `pipeline.request_ptt_toggle` exists and its docstring claims it is "the jarvis-bar's square button", but a repository-wide search finds **no caller**. `jarvis/ui/jarvisbar/interaction.py::resolve_click` returns only `hangup` / `mute` / `talk` / `none` |

### 3.2 Capture

| Feature | Reference tools | Our state | Evidence / anchor |
|---|---|---|---|
| Record raw microphone audio with no silence endpoint | all | **have** | `pipeline._ptt_session` drains `MicrophoneCapture` into a buffer, bypassing the VAD entirely — the key is the endpoint |
| Transcribe-only session that never reaches the brain | all | **have** | `pipeline._dictation_session` + `start_dictation` / `stop_dictation`; publishes `DictationTranscript(text, is_final)` and nothing else |
| Capability probe before offering the feature | Handy, VoiceInk | **have** | `pipeline.dictation_available()` — STT present, input device not `"none"`, capture permission granted |
| Voice-activity filtering of silence | Handy (Silero VAD) | **have** (different placement) | `jarvis/audio/vad.py` — used by the voice path; the dictation path deliberately bypasses it |
| Maximum-hold safety cap | Handy | **have** | `pipeline._ptt_max_hold_s = 60.0`; the dictation lane has `_dictation_max_s` |
| Minimum-hold guard (accidental tap) | none of them (Handy explicitly has none) | **have** | `_ptt_session` discards anything shorter than 300 ms of audio |
| Live partial transcript while speaking | Handy, commercial A/B | **partial — and quadratic** | `pipeline._dictation_session._probe` and `pipeline._ptt_live_transcribe` re-transcribe the **entire growing buffer** every `_ptt_partial_interval_s` (1.2 s). Correct for a short chat utterance, unusable for a two-minute dictation. Anchor for the fix: the same two functions, plus `jarvis/audio/vad.py` for segment boundaries |

### 3.3 Recognition

| Feature | Reference tools | Our state | Evidence / anchor |
|---|---|---|---|
| Local, offline recognition | Handy, VoiceInk, OpenWhispr | **have** | `jarvis/plugins/stt/fwhisper.py::FasterWhisperProvider` |
| Cloud recognition with the user's own key | OpenWhispr (BYOK) | **have, and broader** | `jarvis/plugins/stt/{groq_api,openai_api,gemini_api,openrouter_stt}.py`, all key-aware through `jarvis.core.config.get_secret` |
| Automatic language detection | Handy (Parakeet V3) | **have** | `STTConfig.language = "auto"` is the shipped default |
| Streaming recognition (partials from the provider) | FnKey; commercial A/B | **missing across the board** | All five providers declare `supports_streaming = False`; Groq's `stream_transcribe` deliberately yields one final result. Any "live" text we show is our own re-transcription, not a provider stream |
| Switch model per situation | VoiceInk, superset in commercial B | **partial** | Provider/model are global config (`[stt]`), not per-dictation-mode |

### 3.4 Post-processing

| Feature | Reference tools | Our state | Evidence / anchor |
|---|---|---|---|
| Custom vocabulary / dictionary | OpenWhispr, VoiceInk, Voquill, both commercial | **have** | `jarvis/speech/stt_dictionary.py::DictionaryStore` + `TranscriptCorrector` + `DictionaryCorrectingSTT`, wired around `_utterance_stt` in the `SpeechPipeline` constructor — so the dictation lane inherits it automatically. REST: `/api/dictionary`. UI: `DictionaryView.tsx` |
| Decoder bias from the dictionary | (Groq-style prompts) | **partial by necessity** | `build_stt_from_config` merges dictionary words into the cloud `prompt`; local faster-whisper must never receive an utterance prompt (hallucination). This is why post-STT correction, not bias, is the provider-agnostic mechanism |
| Filler-word removal | Ghost Pepper, Voquill, VOXD, both commercial | **missing** | Nearest existing pattern to copy the *shape* from: `jarvis/brain/output_filter.py::scrub_for_voice` — regex only, no model call (AP-11) |
| Punctuation / list formatting | commercial A/B | **missing** | Same anchor as above |
| Optional LLM cleanup pass | Handy (`llm_client.rs`), OpenWhispr, VoiceInk, both commercial | **missing, but every building block exists** | `jarvis/brain/factory.py` already resolves a key-aware provider chain; the ack-brain (`build_ack_brain`) is the precedent for a small fast side-model |
| Per-app formatting rules / modes | VoiceInk (Power Mode), Amical, commercial B (up to nine modes) | **missing** | Window context is available: `jarvis/awareness/watchers/window.py` |
| Send the dictation to an AI agent instead of a text field | OpenWhispr (second hotkey) | **have via a different door** | That is what the existing voice path does; a dictation-to-agent hotkey would be a third keybind action reusing `pipeline._handle_utterance` |

### 3.5 Insertion — the part that actually decides whether this feature works

| Feature | Reference tools | Our state | Evidence / anchor |
|---|---|---|---|
| Write text to the system clipboard | all | **have** | `jarvis/platform/clipboard.py::write_text` — Win32 `SetClipboardData` with retry, `pbcopy`, `wl-copy`/`xclip`/`xsel` |
| Read the clipboard back | Handy (to restore it) | **have, text only** | `clipboard.py::read_text` — returns `None` for "unreachable" vs `""` for "genuinely empty", which is exactly the distinction a restore needs |
| **Save and restore the previous clipboard** | Handy (text first, image only if no text) | **missing** | Anchor: new `jarvis/dictation/insert.py`, built on the two functions above. Known limitation to document honestly: our clipboard layer is text-only, so a previously copied image cannot be restored |
| Send the paste shortcut | Handy (Enigo) | **have** | `jarvis/cu/actuate/base.py::get_actuator()` → `Actuator.key_combo`; Windows path is native `SendInput` (`jarvis/cu/actuate/windows.py::WindowsActuator.key_combo`) |
| Terminal-safe paste variant | Handy (Shift+Insert, "more universal for terminal applications") | **missing** | Same anchor; the key vocabulary already contains `insert` (`jarvis/cu/actuate/base.py::_NAMED_KEYS`) |
| Type text directly as a fallback | Handy, nerd-dictation | **have** | `WindowsActuator.type_text` uses `KEYEVENTF_UNICODE` and correctly splits astral-plane characters into UTF-16 surrogate pairs; POSIX goes through pynput/pyautogui |
| Detect that insertion *cannot* work before trying | none of the references do this | **partial, wrong direction** | `jarvis/platform/input_isolation.py::describe_input_isolation` answers "can other apps type into **our** window" (Windows UIPI / POSIX root). The inverse probe — can *we* type into the **foreground** window — does not exist yet. The Win32 recipe is the same three calls (`GetForegroundWindow` → `GetWindowThreadProcessId` → `GetTokenInformation(TokenElevation)`) |
| Verify the text actually landed | none | **missing** | Deliberate recommendation in the plan: do **not** try to verify; instead guarantee the clipboard fallback and report honestly |
| Route the text to our own window vs. a foreign app | VoiceInk, commercial A/B | **missing** | Anchor: new module; the foreground-window read already exists in `jarvis/awareness/watchers/window.py` |

### 3.6 Feedback and UI

| Feature | Reference tools | Our state | Evidence / anchor |
|---|---|---|---|
| On-screen indicator while recording | all | **have** | Jarvis Bar: `ui/orb/bus_bridge.py::OrbBridge._on_state` maps `SystemStateChanged` to the coarse modes `idle` / `listen` / `think` / `speak`; `jarvis/ui/jarvisbar/renderer.py::visual_mode` derives the look |
| Live level meter | Handy, commercial A/B | **have** | `jarvis/audio/mic_level.py`; both the PTT drain and the session input stream feed it |
| Live transcript strip on the indicator | commercial A | **partial — plumbing exists, not connected** | `show_listening_transcript(text, duration_ms)` is implemented on all four overlay surfaces (`overlay.py`, `qt_overlay.py`, `subprocess_overlay.py`, `null_overlay.py`) and is fed today from `TranscriptionUpdate`. `DictationTranscript` is **not subscribed** anywhere in `ui/orb/bus_bridge.py` |
| A dictation state distinct from the voice states | all | **missing** | Anchor: a new subscriber in `OrbBridge.attach` plus one new branch in `renderer.visual_mode`; the four existing branches stay untouched |
| Dictation history | VoiceInk, OpenWhispr, commercial A/B | **missing** | Anchor: new `jarvis/ui/web/dictation_routes.py` + a `DictationView.tsx`; the section-registration pattern to copy is `DictionaryView.tsx` + `SECTION_IDS` in `src/store/events.ts` + `Sidebar.tsx` + `nav.*` i18n keys in `en/de/es.json` |
| Settings surface | all | **partial** | Keybinds have one (`settings_routes.py`), dictation has none |
| Works when the app window is hidden | all | **have** | `pipeline._activation_allowed()` checks only mute + capture permission — it does **not** require a visible window, despite the misleading log line "PTT press ignored: Desktop-App not visible." in `_on_ptt_press` |

### 3.7 Control surface (our own rules, not the references')

| Feature | Our state | Evidence / anchor |
|---|---|---|
| Every action reachable over REST → automatic CLI command | **partial — a real gap** | Dictation today is a WebSocket command only: `jarvis/ui/web/server.py::_handle_dictation`, schema in `jarvis/ui/web/schema.py`, consumed by `useWebSocket.ts`. There is no `*_routes.py`, so `jarvis api …` cannot start or stop dictation. Under CLAUDE.md §5 ("CLI-first feature contract") a UI-only feature is not done |
| Voice-command reachability | **missing** | `jarvis/commands/registry.py` has no dictation entry |
| Brand name never hardcoded | **have** | `jarvis/brain/assistant_name.py::agent_brand`; the frontend counterpart is `src/lib/agentBrand.ts`. Any dictation UI string must go through them |

---

## 4. What the reference tools have that we should *not* copy

* **Silent success.** Handy's paste path has no failure signal at all; if the
  keystroke is dropped by the OS the text is simply gone. Our doctrine (AD-OE6,
  "zero silent drops") requires the opposite.
* **A model rewriting the user's words by default.** Both commercial references
  auto-edit aggressively. That is a product decision with a real failure mode
  (content words disappearing) and belongs behind a switch, not in the default
  path.
* **Naming a fixed assistant/brand string in UI text.** Several references brand
  their overlay. Ours is derived from the configured wake word (CLAUDE.md §4).

---

## 5. Where the search stopped

Research was goal-bound, not volume-bound. Searching stopped when additional
sources stopped changing the matrix:

* **Insertion mechanism** — stopped after Handy's `clipboard.rs` + `input.rs`.
  Every further tool checked (whisper-writer, hyprwhspr, VOXD, Voquill) repeats
  the same clipboard-plus-shortcut pattern with the same Linux tool chain
  (`wtype` / `ydotool` / `xdotool`). nerd-dictation's keystroke-only approach is
  the single dissent and is already recorded above.
* **Hotkey handling** — stopped after Handy's `shortcut/handler.rs` plus our own
  four backends. The press/release contract is identical everywhere; the only
  real variable is what each OS permits, which is answered in the plan, not here.
* **Post-processing** — stopped after Handy's `llm_client.rs` and the Ghost
  Pepper / VoiceInk / commercial descriptions. All of them are "optional model
  pass with a caller-supplied prompt"; none publishes a filler-word list worth
  adopting, so ours has to be written and curated regardless.
* **Not investigated, deliberately:** mobile projects (Transcribro, WhisperBoard,
  Whisper IME, Offline Voice Input) — no shared surface with a desktop dictation
  mode; and file/batch transcription tools (Buzz, Vibe) — a different feature.
* **Could not be verified first-hand in this session:** anything requiring a Mac
  or a Wayland session. Those rows are marked in the plan as source-backed rather
  than measured.

---

## 6. Summary

Across the seven tables in §3: roughly **twenty rows are already in place, ten
are partial, and fourteen are missing** — and not one of the missing items
requires a new subsystem. Dictation is a second entry point into machinery this
project already ships.

The three rows that carry real risk are insertion reliability, per-OS hotkey
parity, and the quadratic live-transcription loop. All three are analysed, with
recommendations, in
[`docs/plans/dictation-mode-plan.md`](../plans/dictation-mode-plan.md).
