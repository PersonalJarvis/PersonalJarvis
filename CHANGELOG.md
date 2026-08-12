# Changelog

All notable changes in Personal Jarvis.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning per [SemVer](https://semver.org/).

---

## [Unreleased]

---

## [1.3.2] — 2026-08-12

The five hundred generated wallpapers are now one click away on any machine,
the Contacts section grew into a real address book, and Gemini keys from
Vertex express are routed automatically everywhere a Google key is used.

### Added

- A "Download library" button in the Wallpaper section. It fetches the
  500-piece generated collection (~190 MB) from the project's asset release,
  shows live progress, unpacks it safely, and fills the grid without a
  restart. The app stays fully usable without it: the bundled original
  remains the default, and a failed download is an honest message with a
  retry, never a broken section.
- Contacts became an actionable live master-detail section: profile fields,
  vCard import/export (UI and CLI), a deep link, and a layout that survives
  narrow panes.
- Google keys are probed once for their home (AI Studio or Vertex express)
  and every Gemini client — chat, realtime voice, wiki embeddings — follows
  that route automatically; the key form and cards explain it.
- The in-app feedback form is back beside the Discord invite.
- The voice bubble in the Agentic IDE shows a subtitle-style live transcript.
- Dictation keeps soft word onsets and lifts quiet utterances before they
  reach speech recognition.

### Fixed

- The emergency voice keeps streaming when a TTS quota blocks the primary.
- Provider errors say "free tier day limit" instead of 500 characters of
  JSON.
- macOS: the input-monitoring probe reads `IOHIDCheckAccess`'s 32-bit enum
  correctly; on Apple Silicon the old 64-bit read could see garbage high
  bits and silently degrade the permission check.
- Agentic IDE: a pane follows the size its agent really got, a briefed
  terminal position no longer spawns a fleet, and background tasks are
  reaped on any early exit.

### Security

- pypdf lifted past PYSEC-2026-3655 and PYSEC-2026-3656.

---

## [1.3.1] — 2026-08-11

This release teaches Jarvis to draw. Ask for a picture in plain conversation
("draw me a flowchart of this") and it renders one on the spot, files it in
the Outputs gallery, and opens the new view that shows it. Runs themselves
now appear as node graphs whose edges trace where the run actually went.
The rest is a broad polish pass: self-repairing terminal panes, wallpapers
you bring yourself, a memory that finds short names again, and a stack of
macOS fixes.

### Added

- On-demand pictures. Ask for a drawing and Jarvis renders the thing under
  discussion as a flow, hierarchy, comparison, timeline, or bar chart. The
  model supplies only labels; the app draws the markup itself, so nothing
  model-written is ever served as HTML. The tool is offered only on turns
  that actually ask for a picture.
- A Visualization section. Every run is drawn as an n8n-style node graph,
  a live run's timeline follows the mission while it works, and a run's
  gallery page leads with its mission map.
- Wallpapers, properly: bring your own picture, mark favourites, and give
  dark and light mode each their own wallpaper. The mode toggle swaps them.
- Your own coding CLIs in the Agentic IDE: register and edit extra terminal
  CLIs right from the terminal step, with documentation on how.

### Changed

- The API-keys view answers "am I done here?" at a glance: a saved key says
  "Key saved" next to a green check, and the get-your-key link names the
  site it points to.
- A fleet spawn that repeats an instruction already running on open panes is
  refused, and the refusal names who is already on it.
- The Command Deck no longer announces finished agents out loud. The bell
  and the on-screen report lane stay.
- Outputs show the user's real request instead of the internal quality
  directive, and light mode got a readability pass with minimum-contrast
  floors in agent terminals and over bright wallpapers.

### Fixed

- Agentic IDE panes behave: overlapping panes detect and repair themselves,
  a pane too narrow for its agent says so, a clamped pane scrolls instead of
  clipping, and nothing changes shape under the pointer or during a
  half-drawn screen.
- The wiki memory finds short names again. "Joy", "Uwe", or "BMW" fell
  below the old keyword floor and were unsearchable; new pages also get
  their multilingual search aliases the moment they are written.
- macOS: terminal panes spawn as login shells, the first Terminal launch
  survives the Automation consent dialog, audio ducking honors never_mute
  and recovers from failed restores, and split UTF-8 characters in terminal
  output no longer garble.
- The managed local realtime server recovers from failures it previously
  could not see, reports honest boot progress, and delivers crash forensics
  with a crash-loop verdict instead of a silent retry.
- A wake call stands alone: speech leading into the wake word is hard-gated
  away from the command that follows (BUG-127).
- Three Win32 defects in the window-focus watcher, and Linux autostart
  entries are now spec-escaped.

---

## [1.3.0] — 2026-08-10

This release makes fully-local voice a plug-and-go experience: the app now
installs and supervises everything itself — including Ollama — connects in
milliseconds instead of seconds, and lets you choose the brain model that
fits your machine. It also adds a desktop wallpaper picker, gives every
dropdown the app's own theme, and withdraws the unfinished ChatGPT
subscription voice option until it works dependably.

### Added

- A desktop wallpaper picker: choose the desktop surface's background from
  a thumbnail catalog, reachable from the sidebar and by voice
  ("wallpaper", "Hintergrundbild", "fondo de pantalla").
- The app installs and starts Ollama itself when local models are selected:
  runtime detection distinguishes not-installed / stopped / running, each
  with the right one-click fix (winget or the official installer on Windows,
  Homebrew on macOS, the official script behind non-interactive sudo on
  Linux — every other case gets an honest refusal naming the fix).
- One click on the self-hosted realtime card now covers the whole local
  chain: Ollama, the brain model download, the server environment, the
  vendored patch, and the proving smoke boot.
- A brain-model picker on the voice-server card: every installed and curated
  model annotated with an honest fits/does-not-fit verdict for this
  machine's accelerator; switching never needs a reinstall and an explicit
  choice that does not fit is refused instead of silently substituted.
- Local model cards reach the whole public Ollama library, not just the
  curated shortlist — any published model can be searched, downloaded, and
  used.
- A lifecycle supervisor for the managed voice server: prewarmed at app
  start, pidfile ownership (PID-reuse safe), start/stop from the card and
  the CLI, and an Ollama keep-alive so the brain model stays resident.
- Honest connect errors on every voice surface: a failed start attempt now
  names the provider and reason instead of silently returning to idle, and
  a deleted install fails fast with the repair action instead of retrying
  for two minutes.

### Changed

- Local realtime connects in milliseconds: "localhost" is pinned to
  127.0.0.1 (the OS resolver's dead IPv6 attempt cost ~2 s per connect),
  and the SDK client and model probe are reused across calls
  (~430 ms more per call before).
- Self-hosted brains get a compact instruction profile (about a third of
  the size, prefix-cache-friendly ordering), cutting per-turn thinking
  time on small local models by several seconds.
- Delegated replies wait for the local server's own readback long enough
  (a declared per-provider budget) instead of muting the answer through a
  text-only fallback.
- Preflight tells the truth on unsupported GPUs ("no supported
  accelerator" instead of a false "0 GB"), and the managed install works
  with venvs created by a different Python.
- The curated local-model catalog was refreshed to the current generation
  and is verified against the live Ollama library.
- Every dropdown in the app now uses the shared themed select instead of
  the operating system's native control, so menus look the same on every
  platform (a guard test keeps native selects from coming back).

### Removed

- The "ChatGPT subscription (Codex)" voice card no longer appears in the
  provider settings: subscription voice over Codex's experimental Realtime
  protocol is not reliable enough to offer yet. An already-selected
  configuration keeps working, and the option returns once the transport
  holds calls dependably.

### Fixed

- A deleted managed install no longer leaves every call chasing a
  nonexistent server for two minutes; stale installs are detected and
  offered a repair.
- Killing the voice server no longer strands orphan processes: process
  groups are terminated with escalation on POSIX, and ownership is
  verified before any kill.
- Embedding models (BGE, GTE, E5, MiniLM and friends) and cloud-hosted
  tags are no longer offered as the local voice brain — the first would
  answer a spoken turn with a vector, the second would quietly break the
  "runs entirely on your machine" promise.

---

## [1.2.3] — 2026-08-07

This release makes live voice calls survive crashes and restarts, lets the
assistant actually use its personal memory in conversation, and polishes the
Agentic IDE and the desktop orb.

### Added

- Added a headless live-call probe and per-session postmortem forensics for
  the realtime voice path, so a broken call can be diagnosed from its record
  instead of reproduced by hand.
- Added the connection handshake to the visible call state — and stopped
  paying for it twice on the metered channel.
- Added terminal zoom helpers to the Agentic IDE and let the voice bubble
  headline wrap to two lines, so the sentence naming the agent stays readable.
- Added a light mode to the agents board.
- Added a self-hosted realtime option hardened for Windows machines without
  the symlink privilege, whose model downloads previously died mid-fetch.

### Changed

- Personal memory now works retrieval-first: every substantive turn searches
  the local knowledge vault (single-digit milliseconds) and a strictness
  verdict decides what may ride along — after an audit found the old
  refuse-to-search default produced two full live days without a single
  injected memory. Questions about your own past ("when was I last…") are
  now recognized and answered from memory instead of guessed at.
- The desktop voice orb now carries the in-app bubble's look, controls, and
  energy, and shows its state inside the sphere instead of a speech bubble.
- The Agentic IDE workspace's chat rail became the project sidebar, and the
  chat view now reads the agent's conversation from the CLI's own record
  instead of transcribing the screen.

### Fixed

- A crashed self-hosted voice server no longer ends the call: the reconnect
  window is sized from measured cold revives (including GPU TTS warm-up),
  the transport pump survives waking mid-rebuild, and the server is revived
  with fault diagnostics armed.
- Live-call transcripts are honest again: hallucinated user turns, subtitle
  outros, and the echo of the assistant's own barge-in cut are no longer
  recorded as real speech, and a transcript that arrives whole instead of
  streamed is accepted.
- The microphone is freed fast after a call ends, a dying socket can no
  longer hold it shut, and a silent line is no longer punished.
- The one-speaker rule is re-asserted every turn, taming the opening
  monologue on subscription voice calls.
- OpenAI models that only speak the Responses API are served instead of
  answering with a fake network apology.
- An ordered pane delivery in the Agentic IDE survives the caller's hangup,
  CLI renames and closes are announced to every open view, and "cannot be
  read" is told apart from "not written yet".
- On macOS, raising an app window via accessibility works again on Sonoma
  and Cocoa apps; on Linux/macOS a cleanly exited terminal child no longer
  reads as crashed.
- Blocking desktop calls were moved off the async event loop, removing
  UI freezes.
- Saying "look at the screen" followed by a content word is no longer
  mistaken for a screen-context request, and a reported "it spawned" is no
  longer misread as a request to spawn an agent.

---

## [1.2.2] — 2026-08-05

This release makes realtime subscription voice and the Agentic IDE more
dependable while adding richer voice controls and knowledge-map views.

### Added

- Added native realtime voice transport with in-app readiness diagnostics,
  input-level feedback, browser microphone controls, and a complete action path.
- Added 3D UltraWiki memory maps with 2D/3D switching on both knowledge surfaces.
- Added Agentic IDE chat-or-terminal workspace choices, voice-orb context, prompt
  receipts, persistent terminal sizing, and clearer working/done state feedback.
- Added a real light theme selectable in Settings, replacing a switch that
  previously changed nothing.
- Added a draggable voice bubble to the Agentic IDE that replaces the fixed
  voice side column and speaks status updates from wherever it sits.
- Added UltraWiki word search over a meaning-neighbourhood of terms, so a
  query finds pages that use related words, not only the literal ones.
- Added the voice orb as a fourth on-screen display style: the glowing sphere
  from the Agentic IDE now also runs as a free-floating desktop window, so it
  can be dragged onto any monitor instead of living inside the app. Switching
  between the mascot and the voice orb applies immediately, without a restart.
  On a Linux session without per-pixel transparency the orb window stays hidden
  with one actionable log line rather than showing an opaque square.
- Added a REST-mounted project and chat library behind the Agentic IDE chat
  surface, so conversations and projects are reachable from the CLI too.
- Added an icon-rail sidebar: the navigation collapses by default and has an
  explicit toggle, giving the workspace more room.
- Added the Agentic IDE chat surface itself: a starting screen, a composer and
  quick actions, so a conversation can begin without opening a terminal pane.
- Added in-app downloading of local brain models, so the Ollama card fetches
  what it needs itself instead of sending you to a terminal.

### Fixed

- Prevented subscription voice from hearing itself, inventing user turns,
  fragmenting replies, losing action responses, or going silent after teardown.
- Preserved the beginning of deliberate push-to-talk and call-hotkey speech,
  including quick follow-ups during the wake-word echo lock.
- Kept screen-context questions out of Computer-Use and routed provider failures
  across available families instead of leaving core paths silent.
- Hardened Agentic IDE pane recovery, scrolling, writer failures, terminal links,
  copy shortcuts, and live-tail switching.
- Repaired autostart, window focus, input capture, and audio ducking on macOS and
  Windows, and made every pickable key bindable as a hotkey on macOS.
- Moved desktop log writing to a dedicated thread so logging can no longer stall
  the thread that emitted the record.
- Stopped the wake-word shape gate from rejecting genuine wakes over its own
  spelling assumptions; verification now relies only on word-agnostic evidence.
- Made subscription realtime calls honest end to end: the surface shows a real
  connecting state during a provider's cold start, the reply language is pinned
  when the call opens, a stalled turn recovers instead of hanging, per-call cost
  is reported truthfully, and the transport pre-warms early and works on macOS
  and Linux, not only Windows.
- Stopped a config write from silently dropping every section it was not asked
  to touch, which could erase provider pins and most of a working configuration.
- Kept the Agentic IDE workspace on one screen with sideways scrolling, made a
  pane drop land where the pointer aims, and stopped a provider card that cannot
  be activated from stacking a wall of identical warnings.
- Made the onboarding local-brain button select the local brain instead of a
  cloud one, and stopped the sidebar painting rows at stale positions on macOS.
- Delivered the opening words of a spoken sentence instead of the fragment that
  survived the recognizer's warm-up, and let a delegated turn own its own answer
  rather than losing it to the turn that handed off.
- Stopped a local model that is still loading from being reported as broken, and
  stopped a text-only local model from advertising vision it does not have.
- Stopped the realtime transport from re-sending the whole instruction block on
  every turn, leaving a finished turn unclosed, or honouring a direct-speech
  clearance after its audio must have ended.
- Made the pre-push secret guard scan with built-in patterns when the privacy
  gate is unavailable, instead of loading nothing and reporting a clean push.

### Changed

- Labelled the Agentic IDE and the Codex subscription realtime route as Beta, so
  their maturity is visible before you rely on them.

---

## [1.2.1] — 2026-07-31

This corrective release reconnects the public 1.2.0 line with the complete
maintained source tree and closes the release, install, and UI verification
gaps found after 1.2.0 was published.

### Added

- Added an experimental ChatGPT subscription path for realtime voice, with
  honest capability checks and fallback behaviour.
- Added the Agentic IDE chat view and a consistent coding-CLI picker when a
  workspace or chat rail opens another agent pane.

### Fixed

- Restored the complete current implementation to the public release line,
  including the UltraWiki Python package and its frontend source.
- Preserved interrupted Agentic IDE pane recovery without mistaking the
  restored pane's own repaint for new work.
- Restored portable `[full]` dependency resolution across supported Python,
  operating-system, and CPU combinations, including Windows on ARM64.
- Fixed the README's package-page asset URL and aligned stale frontend tests
  with the shipped UI.

---

## [1.2.0] — 2026-07-30

This is the first public release since 1.1.5 and it is a large one: a
voice-driven workspace for coding agents, a personal knowledge base, voice
that can run entirely offline, and a dictation pass that tidies your wording.

### Added

- **Voice input and voice output can now run entirely on your own machine — no
  API key, no cloud account, nothing leaving the device.** Three new providers
  appear in the API-Keys view, each marked *Local · no key needed*:

  - **Whisper (on this machine)** — OpenAI's Whisper `large-v3`, the full
    multilingual model, for the highest accuracy. One-time download of about
    3 GB; noticeably slower than a hosted provider on a machine without a
    graphics card, which is the price of keeping everything local.
  - **Nemotron (on this machine)** — NVIDIA's Nemotron 3.5 streaming model.
    Covers 40 languages including German, downloads about 690 MB instead of
    3 GB, and transcribes several times faster than real time on a plain CPU.
    No NVIDIA hardware required despite the name.
  - **Piper (on this machine)** — the established offline voice engine, about
    200 MB, with one voice each for German, English and Spanish. It speaks
    faster than real time on a CPU. It sounds good rather than
    indistinguishable from a person; a hosted voice is still the more natural
    option.

  **Everything is installed from inside the app.** Each card says honestly
  whether its engine and model are actually on this machine, and offers a
  single button that fetches what is missing. A provider whose files are not
  there cannot be activated at all — instead of being switched on and then
  failing silently on the first sentence.

  **It degrades honestly.** If you select a local provider and later start
  Jarvis on a machine where it is not installed, voice input crosses to
  whichever cloud provider you do have a key for rather than going dead. Local
  speech recognition also tells the rest of the app that your words never left
  the device, so the optional dictation clean-up stays local too.

- **Dictation now tidies up your wording, and this is ON by default.** After a
  dictation is transcribed, a fast model rewrites the *structure* of the text —
  punctuation, capitalization, filler words and sentence breaks — before it
  lands in whatever you were typing in. Your exact words and their meaning are
  kept; the pass is not allowed to rephrase you, and a rewrite that drifts too
  far from what you said is discarded and your own text delivered instead.

  **Where your text goes.** While the pass is on, the finished transcript is
  sent to the model you selected. If your speech recognition already runs on
  your own machine, the pass stays there too and never crosses to a cloud
  provider on its own — picking a cloud model in the dropdown is the deliberate
  exception. On an install with no text-model key at all, nothing happens and
  the raw transcript is delivered exactly as before.

  **Your raw text is always kept.** Every dictation stores what was actually
  recognized alongside what was delivered, and the history shows both, so a
  rewrite you dislike is never a loss. Each row also says what the pass did to
  it, including when it did nothing and why.

  **Where the switch is:** the Voice section → *Language* tab → *Clean up my
  wording*. Turning it off is immediate and needs no restart. The same tab
  chooses which model family answers, and a *Test* button runs one fixed sample
  through your own setup so you can see the before and after before trusting it
  with your words.

- **Agentic IDE — a voice-driven workspace for coding agents.** Open several
  named terminal panes, each running a different coding CLI (Codex, Claude,
  Gemini and others), then split, drag and resize them freely. Address a pane
  by its name, spoken or typed, and the instruction goes to that agent; one
  request can brief a whole fleet at once and reports back which agents
  actually received it. Drop files onto a pane and they are analysed before
  they become part of the prompt. Workspaces and sessions survive a restart
  and offer to resume. When a spoken call-sign is unclear, the assistant asks
  once instead of guessing — and says so plainly when an instruction reached
  nobody.
- **Screen Context — private, one-shot visual context for voice and chat.**
  Jarvis can capture the active screen only when explicitly asked, shows a
  visible capture indicator, redacts password fields and sensitive text, and
  never stores the image. Windows, macOS and Linux/X11 are supported; Wayland
  and headless systems fail closed with an honest explanation. Screen
  observation remains separate from desktop-control permission.
- **UltraWiki — a personal knowledge base built from your own material.** A
  staged import pipeline, hybrid keyword and semantic search, and an Explore
  view with topics, moments and a graph. Readers cover local folders, GitHub
  issues and pull requests, cloud-storage attachments, phone media (with
  image description and audio transcription), generic HTTP and RSS sources,
  and Obsidian vault import and export.

  What it needs: semantic search requires one embedding backend — Ollama runs
  locally with no key at all, or use a Gemini, OpenAI, Voyage, Mistral or
  Cohere key. Without one, search runs on keywords only and the built-in
  health checklist says so rather than failing quietly. Storage is SQLite by
  default and needs no setup; Postgres and Supabase are optional
  (`pip install "personal-jarvis[ultrawiki-postgres]"`). Your own content
  never leaves your machine except to the providers you configure.
  UltraWiki can now answer questions with real citations, returns
  `insufficient_evidence` instead of inventing a source, keeps configured
  folders fresh automatically, and lets users edit source settings in-app.
- **Local and subscription brains.** A generic provider for any server
  speaking the OpenAI chat API (llama.cpp, vLLM, LM Studio, HF serve), a
  keyless local Ollama provider, and an Anthropic-subscription option — a
  fully offline or self-hosted setup is now a real path.
- **Marketplace connectors**, including Home Assistant, plus a self-hosted
  server option for supported services.

### Changed

- Wake-word verification no longer assumes the wake phrase is spoken in
  German; it follows the language actually in use.
- Skill routing is deterministic and relevance-scored, and mission workers
  now reach the same knowledge the voice assistant does.
- A coding CLI is a registry entry rather than a hardcoded path, so a newly
  supported agent is reachable by voice the day it lands.
- UltraWiki's background import paces itself against a configurable share of
  the machine instead of monopolising a core.
- The in-app documentation now covers 50 maintained guides, including
  Dictation, Agentic IDE, Screen Context, UltraWiki, local AI and Home
  Assistant.

### Fixed

- A dictation that lost part of its audio no longer reports itself as a
  success. It is marked as partly transcribed, the recording is kept, and the
  history offers to transcribe it again.
- **Dictated text lands where you were actually typing.** The transcript now
  carries the target the pipeline resolved, so a dictation meant for another
  program is no longer written into whatever Jarvis field last had focus —
  invisibly, in a section you were not even looking at.
- **A finished terminal pane now rings the bell.** The notification used to
  wait for something a particular coding CLI prints — first an interrupt hint,
  then a running clock — and neither is printed by every product in every
  phase, so panes finished all around you and announced nothing. A pane is now
  judged by whether its screen is still moving, which is true of any terminal
  and of a CLI nobody has taught it about yet. A pane that ends says why.
- **A skill imported from a link no longer activates itself.** Imported
  skills are stored as drafts and stay inactive until explicitly enabled, and
  a downloaded file can no longer declare itself active.
- A misheard agent name can no longer deliver an instruction to the wrong
  terminal.
- A denied tool call inside a mission is treated as a recoverable setback
  rather than ending the whole mission.
- Sub-second audio gaps mid-answer, a realtime fallback misreported as a
  hangup, and a turn-planner failure ending a live call.
- A spoken mention of an unrelated product name could unlock desktop control;
  dictation and auto-type tools went dead while the app ran elevated.
- Disconnecting a marketplace plugin now revokes the grant at the provider
  instead of only forgetting it locally.
- **A dictation is no longer punished for being corrected.** The safety check
  that watches for words going missing counted a repair as a loss, so
  "deskto" becoming "desktop" was treated as a deleted word and the tidied
  text was thrown away — taking the corrections you wanted with it. A word
  that reappears spelled correctly now counts as fixed, while a word that
  genuinely vanishes still stops the pass.
- **Dictation recognises the spoken language reliably**, and translation
  works out of any supported language rather than only out of English, with
  the two ends no longer swapped.
- **The on-device voice speaks the right language.** Switching to the local
  Piper voice inherited a pinned German setting from whatever provider came
  before, so every answer came out in German — including the ones meant to be
  English or Spanish. A downloaded voice you picked yourself is also kept
  across a switch instead of being reset.
- **A terminal pane no longer shows a screen full of garbled characters**
  when you return to it: the replay was being drawn over what was already
  there instead of on a cleared screen. The pane scrollbar also works in
  every CLI now, rather than only the one whose wording it was reading.
- **The app starts and responds faster.** A catalogue of installed extensions
  was being re-read from disk on every lookup — a sweep across hundreds of
  packages that blocked the app for seconds at a time. It is read once. A
  stall in the interface now also names the code responsible, instead of
  reporting that something, somewhere, was slow.
- Startup now recovers cleanly from malformed structured environment values
  instead of leaving the desktop app unable to open.
- Chat history once again prefers real voice sessions, hides phantom text
  threads with no user message, and persists new text conversations without
  duplicate websocket output.
- UltraWiki no longer imports linked worktrees or retains raw content in
  deletion tombstones. Legacy tombstones self-heal, stale source timestamps
  are corrected, and Gemini capability probing no longer repeats a rejected
  request for every background summary.

---

## [1.1.5] — 2026-07-26

### Added

- **Personal Jarvis is on PyPI.** `pipx install personal-jarvis` (or
  `pip install personal-jarvis`) now installs it from the package index, so
  no clone and no Git URL are needed. The one-line installer stays the
  recommended path; this is the isolated, any-OS alternative.
- The project page carries the full README with its images, the license, the
  supported Python versions and operating systems, and links to the website,
  the repository, the changelog, Discord, and the issue tracker.

### Changed

- Every `v*` tag now publishes to PyPI automatically through GitHub Actions
  using Trusted Publishing — no API token and no repository secret is stored
  anywhere. A tag whose version disagrees with the packaged one fails the
  build instead of publishing an untraceable release.
- The README's images and document links are absolute URLs, because PyPI
  renders the README on its own domain and does not resolve repo-relative
  paths — they would otherwise be broken images and dead links.

## [1.1.4] — 2026-07-23

### Added

- **The bar is now yours to size.** A new "Bar size" slider in
  Settings → Appearance live-resizes the JarvisBar and every surface it owns,
  remembers your choice, and defaults to a larger, physical-size-consistent
  135% so the bar looks the same on any screen. It is also reachable over the
  API (`GET`/`PUT /api/settings/bar-size`).
- **The bar follows you across monitors.** It moves to the monitor your mouse
  is on, and dragging it from one screen to another now works correctly.
- **Two more ways to run speech-to-text on a single key.** OpenAI Whisper and
  Gemini cloud STT plugins let a downloader whose only credential is an OpenAI
  or Google key dictate without a separate transcription provider.
- A discreet, honest hint under the engine switch explains that single-mode
  is about which API keys you hold, not a lesser mode.

### Changed

- **Opening an app now fills the screen.** `open_app` maximizes a freshly
  launched window, and also maximizes an already-running one when it is
  brought to the front.

### Fixed

- **Google Drive works again.** Drive now talks to Google's REST API directly
  with the full scope instead of the hosted Drive connector, which only served
  Workspace preview accounts and returned "403" for everyone else.
- **A single plugin no longer takes the main brain down.** One tool's schema
  used an object-key constraint (`propertyNames`) that Gemini rejects, which
  had been failing the whole request and cascading through the provider chain;
  those constraints are now stripped before the call.
- **Hanging up from the bar can no longer freeze it or deafen the wake word.**
  A realtime session's teardown is now time-bounded, so closing a call with
  the bar's X no longer stalls on a slow provider or leaves the bar stuck
  "listening".
- **Escape and hang-up abort an on-screen action instantly**, even mid-step
  while Computer-Use is still thinking, instead of only between steps.
- **The wiki stops crying wolf.** A content judgment during curation is no
  longer misreported as a provider chain failure, so the big red banner no
  longer appears when nothing is actually broken.
- Background text jobs cross to a different provider family when the default
  Claude API path has no key, instead of dead-ending.
- Telegram and Discord user-facing strings are now English.
- On Linux, Computer-Use honestly reports characters it had to drop instead of
  claiming success.
- The custom wake-word onboarding copy is corrected and now provisions the
  real Vosk fallback.
- Delegated action turns answer in the same language as the rest of the
  conversation.
- Computer-Use recovers from Gemini's "thinking required" errors and no longer
  falls back to a blind last-resort vision pass.

## [1.1.3] — 2026-07-21

### Added

- **The wiki now learns who you are from how you talk, not only from literal
  statements.** Captured facts carry an evidence tier (explicit / behavioral)
  and a personal-salience score: "I love being out on golf courses with my
  buddies" now yields an `*(inferred)*` profile bullet, while low-value world
  knowledge stays out of the vault. A one-shot `recurate-profile` CLI
  (dry-run by default, snapshot first) re-judges an existing profile, and an
  expandable memory-map view shows the vault's structure (ADR-0029).
- **Spoken failures now name their real cause**, and calendar trivia
  ("what day is tomorrow?") is answered natively instead of being delegated.

### Changed

- **The assistant acts only on an explicit ask.** Background agents spawn
  only when you ask for one (deterministic gate + a notice in the Agents and
  Outputs views), and Computer-Use missions start only when you explicitly
  ask for an on-screen action — a knowledge question is answered directly or
  via web search, never by driving your browser (BUG-107).
- **One voice per call.** The short-lived escalation that rendered delegated
  replies through the surface TTS was reverted: the native realtime voice
  speaks every reply in a session (BUG-086).
- The API-Keys view uses a compact layout on laptop screens, tells the truth
  about shared/fallback keys, and warns before deleting a key other features
  still depend on.

### Fixed

- **Realtime calls survive provider transport resets.** The transport is
  rebuilt proactively inside Gemini's GoAway window, the rebuild's
  conversation seed is accepted again (BUG-104), and a raced disconnect no
  longer kills the call mid-sentence.
- **Realtime answers are honest and audible.** The live model no longer
  asserts stale pre-cutoff facts as current, no longer invents niche figures
  or drifts to a misheard sound-alike entity (BUG-106), no longer replays an
  already-delivered answer, and the 1-2 s post-turn deaf window on the
  desktop microphone path is closed. Mid-sentence provider pauses are
  de-clicked, speaker echo no longer confirms as barge-in or doubles the
  answer in a second voice, and the voice bars move in sync with the voice.
- **Computer-Use missions know what they are doing.** A corrective follow-up
  ("do it in my Chrome browser") now carries the original task and its
  constraints instead of executing the correction's literal words (BUG-105),
  and recent missions are visible as context to the next one. macOS action
  loops are much faster: bounded accessibility-tree walks, a capped
  focus probe (kills a 15-second open-app stall), and batched actions no
  longer refused by foreground-signature churn.
- **macOS Keychain dialogs are quiet again.** Secrets collapse into one vault
  item accessed through the Apple-signed security CLI — no more
  password-dialog storms at boot (BUG-103).
- **Updates preserve your data on every path.** The wiki vault is salvaged
  and restored across updates and resets, with a snapshot taken first and no
  silent failures.
- **Providers and credentials behave on every setup.** Brain tiers can read a
  realtime-scoped key as a last resort, native Claude subscription login is
  honored, tool models honor `reasoning_effort` (fixes an OpenAI
  empty-output crash), rejected optional parameters are remembered per
  endpoint instead of retried every step, and realtime quota exhaustion
  falls back across provider families.
- **The wiki background curator no longer loops.** Judge-rejected batches are
  bisected, repeated rejections back off and park the poisoned row, a dead
  session's vault lock is stolen immediately, and template/code scaffolding
  no longer pollutes the memory map.
- **Boot and wake are snappier.** The multi-second Vosk wake spawn delay on a
  busy CPU is gone and heavy imports left the boot path — voice-ready wall
  time on the reference Mac dropped from ~28 s to ~14.5 s.
- Fresh installs prefetch complete local voice models, and `open_app`
  recognizes installed macOS/Linux apps the whitelist misses.

### Removed

- **The desktop push-to-talk shortcut has been retired.** Voice Keybinds now
  contains only Call and Hangup. Legacy push-to-talk values remain readable so
  existing configuration files still boot, but they are no longer registered
  or exposed through the Settings API.

## [1.1.2] — 2026-07-20

### Fixed

- **Audio input and voice-output devices now stay accurate after hardware
  changes.** The settings picker refreshes safely when microphones, speakers,
  headsets, or virtual devices are connected or removed, shows the actual
  input/output devices on the current OS, and preserves a valid selection
  without requiring an app restart.
- **Wake, push-to-talk, and realtime voice recover instead of going silent.**
  Wake-microphone reopen failures and stalled reads now trigger bounded
  recovery, push-to-talk release is reliable, microphone-send stalls rebuild
  cleanly, stale rebuild timeouts are ignored, and novel follow-up speech is
  preserved while self-playback and barge-in are cancelled atomically.
- **Fresh-install model choices and fallbacks are respected.** A model selected
  through the provider UI is no longer overwritten when no explicit router
  section exists, same-provider fallback models remain available, coding-CLI
  readiness/login state is truthful, and tests no longer inherit a maintainer
  machine's private provider configuration or credentials.
- **Mission results and desktop workflows fail honestly and remain usable.**
  Mission outcome handling, connected coding-CLI flows, saved-file drag-out,
  and macOS Computer-Use indicator behavior were hardened across success,
  retry, and unavailable-capability paths.
- **Wiki capture uses one canonical entity taxonomy.** Bundled vault templates,
  graph ingestion, and desktop rendering now agree on entity types, while
  newly captured facts surface consistently in the graph.
- **Cross-platform setup and validation are more portable.** Non-Windows
  Registry operations return an explicit unsupported result, subprocess and
  stdio checks recognize the running virtual-environment interpreter without
  relying on PATH, and simulated Windows paths keep Windows separators on
  macOS/Linux CI. Skill-draft traversal checks now reject both separator styles.
- **macOS: Jarvis Bar hover controls now react and remain stable.** The
  non-activating Qt panel could miss mouse-move events, while replacing its
  alpha input mask emitted a false leave as the pill expanded. The result was
  an unresponsive or flickering hover state whose close and microphone controls
  could not be used reliably. The panel now explicitly accepts mouse movement
  and reconciles the real cursor against a stable pill footprint, preserving
  distinct mouse-out and mouse-over visuals during idle, listening, thinking,
  and speaking states (BUG-095).
- **macOS: the Jarvis Bar now follows the Dock's real visibility instead of an
  invisible work-area boundary.** Fullscreen Spaces can hide the Dock while Qt
  continues to reserve its 57-pixel strip, which prevented the bar from being
  dragged to the true bottom of the app. The bar now uses the complete screen
  edge while the Dock is hidden, retreats above it when it returns, and restores
  the user's preferred position when it hides again. Menu-bar and multi-display
  safe areas remain respected (BUG-094).
- **macOS: the Jarvis Bar is transparent and animation frames no longer pile
  up.** Aqua-Tk 9 kept an opaque black Canvas backing and composited every new
  RGBA frame over the old one, producing a rectangle at rest and concentric
  red/green/gold outlines while speaking. The macOS companion now uses Qt's
  translucent surface with full-frame alpha replacement; Windows and Linux
  keep the established Tk color-key path unchanged. Bar clicks are also
  executed in the parent process again, transparent window padding passes
  clicks through to the app underneath, the companion no longer steals macOS
  foreground focus every 500 ms, and parent TTS loudness now reaches the
  companion equalizer (BUG-093).
- **macOS: the repeated Keychain password-dialog storm is stopped.** A Control
  key created by an older direct Python launch could make macOS ask for the
  login-keychain password again on every protected request — often dozens of
  identical dialogs, with **Always Allow** unavailable because that Python
  executable had no verifiable signature. Jarvis now performs one serialized
  credential read per process and, after the one necessary approval, safely
  re-creates that legacy item under the verified installed app identity. Normal
  restarts and source updates then reuse the app-owned item without asking
  again; direct development launches cannot weaken or claim its access rules.
- **macOS: the uninstall command works again.** On a Mac, the documented
  one-liner `bash ~/.personal-jarvis/install/uninstall.sh` printed a syntax
  error and did nothing at all — no prompt, no removal. macOS still ships a
  2007 version of the shell the script is written for, and one line of the
  uninstaller was written in a way only newer versions understand, so the file
  could not even be read, let alone run. Affects installs on 1.1.0 and 1.1.1;
  Windows and Linux were never affected. If you are stuck on an affected Mac
  and want to uninstall before updating, this does the same job and skips the
  broken script: `~/.personal-jarvis/.venv/bin/python -m jarvis --uninstall`.
- **Shell scripts are now checked against the shell macOS actually ships.**
  Nothing in the automated checks had ever done that, which is why the dead
  uninstaller shipped twice while every test stayed green. Every shell script
  in the project is now verified to be readable by that older version before a
  change can land.

## [1.1.1] — 2026-07-19

### Fixed

- **macOS: realtime voice no longer talks to itself.** On built-in speakers
  next to the built-in mic, the assistant's own playback could come back as
  a "user" turn and be answered — spiralling into an endless two-voice
  self-conversation (BUG-089). Realtime sessions now recognize their own
  recently spoken words (including every canned error phrase) and drop the
  echo before it can start a turn; a genuine answer that adds anything new
  always gets through.
- **Outage apologies stop repeating themselves.** When no language model is
  reachable, the spoken "I can't reach my language model" notice now comes
  at most once per half minute instead of on every turn — repeats complete
  silently and honestly in the log.
- **The emergency fallback voice keeps the caller's voice profile.** When
  the realtime voice dies mid-call and a reply is re-rendered locally, the
  substitute voice now matches the session voice's gender instead of
  hard-flipping to a male default — no more "second assistant" joining the
  call.
- **The fallback voice also stops re-rolling its delivery mid-answer.**
  The local re-render now speaks a reply as ONE take (honoring the
  configured voice-consistency knobs), so a long answer can no longer
  audibly change character between sentences (BUG-090); session records
  now label each turn with the voice that actually spoke it.
- **Smoother replies while you can interrupt.** The local interrupt
  detector's per-frame inference moved off the audio loop — one less
  stutter source on slower machines.
- **macOS: the status bar comes back after a crash.** The out-of-process
  bar host now respawns itself (bounded, with honest logging) instead of
  staying invisible for the rest of the session.
- The session build warns when the realtime provider and every configured
  brain provider share one credential family — the setup in which a single
  quota error silences both at once.

## [1.1.0] — 2026-07-18

### Added

- **Desktop control is on by default for fresh installs.** New installs can
  ask Jarvis to operate the computer out of the box; every action still runs
  through the safety tiers, and the switch remains in Settings.
- **Live "Test" button on the Claude / Codex / Antigravity agent cards** —
  verify a connected coding CLI actually responds, right from Settings.
- **A release-safety gate against half-shipped UI bundles.** Commits and
  pushes are now blocked automatically when the shipped web UI references
  files that were never added to the repository — the failure previously
  produced a permanently blank window on every fresh install.
- **macOS: Keychain access is a first-class permission.** The permissions
  view now surfaces it with guidance instead of leaving credential saves to
  fail silently.
- **Wiki memory keeps up with realtime voice.** Conversations held in
  realtime mode are swept into the wiki by an evidence-safe background
  backfill, and a profile update can create its missing topic page in the
  same batch.

### Changed

- **Conversation mode is now the default.** After Jarvis answers, the mic
  stays open for a natural follow-up; one-turn-per-wake becomes an explicit
  opt-in (`[trigger].single_turn_mode = true`). The developer speech CLI now
  honors the same setting.
- Field-tuned routing defaults (spawn / smalltalk / marker lists) now ship
  for every install instead of living only in the maintainer's local config.

### Fixed

- **Jarvis no longer answers its own voice.** Speaker output picked up by
  the microphone could start a phantom turn (BUG-084); the echo is now
  suppressed at the source.
- **One-click updates got honest and resilient.** A transient release-check
  failure no longer breaks the update button; a staged-but-unfinished update
  is surfaced as "finish the update" instead of silently starting over; a
  failed install after restart reports the rollback instead of pretending
  nothing happened; the status overlay never offers a non-newer version.
- **The uninstaller stops the running app first** instead of failing to
  delete files that were still in use.
- **The model picker no longer crashes on fresh installs** that have no
  `[brain.providers]` section yet.
- **Installed coding CLIs are detected reliably** even when the desktop
  app's environment lacks their install directories on PATH.
- **macOS: the transparent window backing is re-asserted on every reveal**,
  with loud diagnostics when the pyobjc layer is missing (BUG-075
  follow-up).
- Dependency security floors: `mcp>=1.28.1`, `json-repair` declared
  explicitly; the Windows-ARM64 `cryptography` exposure is documented.
- **Realtime voice sessions survive connection rebuilds intact.** A rebuilt
  provider transport now inherits the running call's transcript and keeps
  one voice identity across every native rendering order; the frozen turn
  is mirrored to the surface, and the session-end event fires from every
  surface, not only the browser (BUG-085/086/088).
- **Wiki capture got failure-proof.** Recently-failed providers are demoted
  to the end of the fallback chain, a slug collision no longer demotes
  valid links (old demotion scars self-heal), and a failing companion
  topic page no longer blocks the primary fact.
- **macOS uninstall no longer triggers a Keychain password-prompt storm**
  during credential removal.

## [1.0.12] — 2026-07-18

### Fixed

- **macOS permissions no longer look "auto-denied" after an app update.**
  Updating rebuilds the app bundle with a new ad-hoc code signature, and macOS
  then orphans every previously recorded permission: Microphone falls back to
  "not asked" while Input Monitoring and Input Control read as silently DENIED
  without ever showing a prompt. The installer now detects the signature
  change and resets its own stale permission entries, so macOS asks fresh
  instead of inheriting a dead denial (BUG-083).
- **"Open Settings" now always lands on the requested privacy pane.** macOS
  System Settings ignores the pane deep link while it is already running and
  just raises whatever pane was open last; Personal Jarvis now closes a
  running System Settings first so it relaunches on the right pane.
- **Screen Recording no longer shows a stale "Not allowed" with a dead Allow
  button.** macOS freezes that permission's status until the app restarts and
  never re-prompts after the first request. The permissions UI now shows
  "Restart pending" instead of the stale state, hides the request button that
  could never prompt again, and keeps the restart call-to-action.
- **Background agents start only on an explicit request.** A deterministic
  gate now enforces the delegation contract at every model-chosen spawn site;
  a plain conversational remark can no longer start a background agent. A
  blocked spawn instructs the model to answer inline and, for genuinely heavy
  tasks, offer delegation — a clear yes then unlocks exactly one spawn.
- **The installer self-heals a stale or broken install directory** instead of
  aborting with an error.
- **Dependency resolution no longer fails on ARM64 Linux**: the on-screen
  indicator's optional Qt dependency is excluded where no compatible wheel
  exists; the indicator degrades to a logged no-op there.

### Changed

- The installer banner mascot was redrawn as hand-drawn pixel art, crisp on
  light and dark terminals.

## [1.0.11] — 2026-07-18

Consolidation release. v1.0.6–v1.0.10 were cut from a separate macOS-focused
line; this release unifies both lines into ONE repository history, so it
carries every fix from both sides. The repository also moves to the standard
shared-history workflow: GitHub secret scanning with push protection is
enabled, and releases now always ship the entire current state.

### Fixed

- **Voice calls no longer end on their own right after Jarvis answers.**
  Three independent causes fixed: the provider dropping its Live WebSocket
  after a long reply now triggers an in-place transport rebuild instead of a
  hang-up (BUG-071); a "hello?" probe while a delegated answer is still being
  computed gets a deterministic wait answer instead of derailing the session
  (BUG-070); and the hang-up gate is re-checked at the moment of speaking, so
  a stale preamble can no longer play into an already-ended call.
- **Delegated realtime answers are ~3.7× faster** (live p50 15.6 s → 4.2 s):
  no thinking on tool rounds, stable per-turn caching, and text-leaked tool
  calls eliminated (BUG-072).
- **Realtime sessions could hang forever on shutdown** when the pump's single
  cancellation was lost mid-await; the bounded wait now re-cancels (BUG-081).
- **Fresh installs no longer show "Model unavailable" for the default
  model.** The Gemini default pointed at a model id the API no longer serves;
  model health checks now probe the exact model the runtime would use, and
  switching models in the picker takes effect reliably.
- **Wake word reliability:** a shape-only offline confirm can no longer win
  against a stronger acoustic candidate, and the boot storm no longer starves
  the wake-model load (wake was deaf for the first minute after boot).
- **macOS JarvisBar renders correctly** — the bar appears without the opaque
  grey box (Tk 9 paints systemTransparent opaque; the native window backing
  is now cleared) and survives Tk 9 init order; the wake engine no longer
  crash-loops on comma-decimal locales; fresh macOS installs no longer crash
  at first launch.
- **Computer-Use:** desktop missions are serialized behind a global actuation
  lock, every mission gets an id with per-id cancel, and silently refused
  guard actions are surfaced instead of swallowed (BUG-082).
- **Sidebar logo shows reliably** — missing assets return an honest 404 and
  the image self-heals instead of rendering broken.
- **Realtime voice no longer pauses or stutters mid-reply.** When the live
  provider's output transcription lagged its audio (Gemini Live: routinely
  3-22 s), the voice-scrub gate held the audio back — first as
  multi-second dead stops mid-word, then (after an interim 400 ms bounded
  hold) as rhythmic block-wise stutter. Mid-reply holds are now removed
  entirely: once the reply's opening transcript has been vetted clean,
  audio flows unconditionally and the scrubber acts as a trailing kill
  switch that still cancels the response on a detected leak. The strict
  fail-closed turn opening is unchanged (BUG-080).

- **macOS 15 no longer kills the app when hotkeys arm or Computer-Use types
  ("Personal Jarvis quit unexpectedly", SIGILL).** pynput resolved the
  keyboard layout through HIToolbox TSM calls on background threads, which
  modern macOS aborts with an uncatchable illegal-instruction trap. Two-layer
  fix: global hotkeys now use a TSM-free Quartz event-tap backend, and a
  main-thread keyboard-layout snapshot is captured at boot so any remaining
  pynput keyboard path (e.g. `keyboard.Controller()` in Computer-Use
  actuation) reuses it instead of touching TSM off-main — degrading to the
  pyautogui fallback, never crashing (BUG-077).

- **macOS installer no longer dies with a bare exit code on uv-provisioned
  Pythons.** The app bundle's native launcher is now an in-repo compiled C
  stub linked against the exact Python runtime in use — replacing the py2app
  alias stub, which only worked on framework Pythons and left Intel Macs
  (uv standalone bootstrap) with an unlaunchable bundle. Desktop-integration
  failures now write `data/logs/install-desktop-integration.log` and print
  the actual error instead of discarding it (BUG-076).
- **Voice endpointing degrades honestly without onnxruntime.** The WebRTC VAD
  fallback tier is now actually wired (Silero ONNX → WebRTC VAD → RMS energy);
  previously the middle tier was documented but never imported, so
  onnxruntime-less systems (e.g. Intel Macs) silently fell back to bare
  energy endpointing (BUG-061 follow-up).
- **The installer's speech-model report is honest** — it reflects which
  models/runtimes are actually usable on the machine instead of assuming the
  full stack, and can always be produced without raising.
- **Windows-only dev scripts refuse to run on other platforms** with a
  one-line message instead of an ImportError traceback.

### Added

- **Optional browser lock.** The local web UI opens without the Control Key
  by default; the lock is now an opt-in setting.
- **Per-voice audio previews** for realtime provider voices in the settings.
- **Computer-Use screen indicator** — a gold glow border while a desktop
  mission runs, with Esc-to-cancel, backed by an in-process run registry.
- **Screen-adaptive JarvisBar sizing** — small screens shrink the bar, large
  monitors keep the approved look.
- **Real macOS menu-bar icon.** The tray icon is hosted on the AppKit main
  thread (pystray `darwin_nsapplication` + `run_detached`), completing the
  BUG-056 follow-up — macOS gets the same tray surface as Windows/Linux.
- **Mascot/orb on macOS.** The mascot/orb overlay now renders in its own
  subprocess host (BUG-057 follow-up), with Aqua-Tk alpha transparency.
- **macOS audio ducking.** Music and Spotify are ducked via AppleScript for
  the duration of a voice session and restored afterwards, with an opt-in
  master-volume fallback.

### Removed

- The stale root-level `install.sh` (legacy clone-then-run script). The one
  advertised one-line installer remains `install/install.sh`.

## [1.0.10] — 2026-07-16

### Fixed

- **The one-line installer works in non-interactive environments again.**
  The welcome gate's terminal probe relied on `test -r/-w`, which passes on
  CI runners and headless automation where `/dev/tty` exists but cannot be
  opened (no controlling terminal) — the piped install aborted before doing
  anything. The gate now probes with a real open and quietly skips the
  question when no terminal is available.

## [1.0.9] — 2026-07-16

### Fixed

- **Headless first boot: onboarding answers again.** The full server's
  security boundary now serves `/api/onboarding/*` without a credential,
  matching the serve-first bootstrap. A fresh install on a headless box
  could otherwise never complete onboarding — the first-boot contract broke
  the moment the full app took over from the bootstrap (caught by the
  fresh-install smoke workflow on the v1.0.8 release commit).

## [1.0.8] — 2026-07-15

### Added

- **macOS permissions surface.** Runtime TCC permission probes (microphone,
  accessibility, screen recording) with a settings panel, an onboarding step,
  REST routes, and a `jarvis` CLI command — degrading to a quiet no-op on
  other platforms. A dedicated macOS desktop CI workflow guards the path.
- **In-app docs system.** Authoring pipeline, docs overview, sidebar and
  full-text search UI, plus a public-docs CI check.
- **Wiki grounding.** Extraction now records bounded, secret-redacted evidence
  excerpts (migrations 0006–0008) with an audit trail and a backfill for
  existing entries.
- **Computer-Use foreground target guard** — actuation tools verify the
  intended window is actually in the foreground before clicking or typing.
- **Brain tool-call recovery** — malformed provider tool calls are repaired
  instead of failing the turn.
- **Persistent realtime voice sessions** stay open between turns, with a
  voice-mode badge in the session UI.

### Changed

- **Unified web-surface security**: one cookie/bearer policy as route-level
  defense in depth, an auth gate in the frontend, and authenticated mission
  WebSockets and terminals.
- **Transactional self-update**: the relauncher applies updates as a guarded
  transaction with rollback on failure.
- Scoped provider credentials, realtime scrub-gate refinements, onboarding /
  socials / settings polish, and WS schema updates.

### Fixed

- **Desktop installs remain discoverable after an in-app update.** Managed
  installs now register and repair the Windows Start-menu and Installed Apps
  entries, the macOS per-user app bundle, or the Linux application-menu entry.
  Installer, updater, first desktop paint, and uninstaller share one guarded
  lifecycle, while headless and developer checkouts remain untouched.

## [1.0.7] — 2026-07-14

### Fixed

- **macOS first boot works.** Three native first-launch aborts ("Python quit
  unexpectedly") fixed in one forensic series: the tray status item, the
  Jarvis bar/orb Tk windows, and the virtual-cursor overlay were created off
  the main thread (AppKit/Aqua-Tk abort natively, BUG-056/057); PortAudio
  re-initialization is now serialized single-flight and the global-hotkey
  event tap preflights the Accessibility grant instead of letting macOS kill
  the process (BUG-058). macOS runs with the desktop window + Dock icon; the
  menu-bar icon and on-screen bar return once main-thread hosting lands.
- **Local speech pack install no longer blames your internet.** A missing
  prebuilt wheel (e.g. Python 3.14 + av) is now diagnosed honestly, pip runs
  wheel-only on end-user machines (never a source build), and the installer
  prefers Python 3.13/3.12 until the native stack ships 3.14 wheels (BUG-059).
- **Grounded wiki answers.** "What is in my wiki" is answered by a new
  deterministic listing tool in one round instead of blind probing; contract
  pages carry a provenance warning; delegated voice turns get a hard
  wall-clock deadline with a forced final answer (BUG-055).
- **Realtime stability.** A benign cancel race no longer ends the call, and
  German (any-language) capability verbs reach connected tools directly
  instead of always spawning an agent.

### Added

- **OAuth token refresh lifecycle** for marketplace plugins (scheduler,
  PKCE/token-store hardening, Gmail/Calendar REST updates).
- **Sessions view rework**: richer session detail and turn cards.

## [1.0.6] — 2026-07-13

### Added

- **Realtime voice engine — first public release** (previously withheld):
  low-latency speech-to-speech conversations with tool delegation, plus the
  tool-model pick and a broad reliability wave.

### Changed

- **A missing Node.js no longer blocks the one-line installer.** Node only
  powers the optional coding-agent worker (Claude Code / Codex) and a few
  Node-based integrations; the installer now notes its absence and continues,
  pointing to the in-app path for adding the worker later — instead of turning
  new users away at the door.

## [1.0.5] — 2026-07-09

### Fixed

- **The wake word now works for non-English speakers.** Wake detection routes to
  a model that matches the language you actually speak — the right-language local
  keyword model, or multilingual Whisper as a fallback — instead of silently
  going deaf on an English-only model. The missing language model is fetched
  automatically on a language switch or boot, wake stays pinned to the CPU, and a
  new **language selector** (with a "Test wake word" readiness check) lets you set
  the spoken language directly in Settings.
- **The Windows taskbar button shows the Jarvis mascot** instead of the generic
  Python logo. The app re-launches through a mascot-branded executable that owns
  its window, and it self-heals a stale Start-Menu shortcut. Best-effort and
  fully guarded: on a read-only or Store-Python install it degrades cleanly with
  no change in behavior.

### Changed

- **Clearer API-keys screen.** NVIDIA NIM is now flagged with a "not recommended"
  caution badge (its free tier is slow), Inworld TTS is no longer mislabeled as a
  realtime provider, and the Pipeline / Realtime voice-engine switch was
  redesigned for clarity.

## [1.0.4] — 2026-07-08

### Fixed

- **Custom wake words work out of the box on a fresh install.** The per-language
  Vosk keyword-spotting model is now provisioned automatically — installer
  prefetch, an off-boot self-heal on first run, and an in-app "Download wake
  model" button — so a freely chosen wake phrase resolves to the reliable
  any-word engine instead of silently degrading to the transcribe-and-match
  path that cannot recognize a hard proper noun. Works for every supported
  language (`en` / `de` / `es`), with no training and no GPU, on any OS
  including Apple Silicon. The word-agnostic openWakeWord backbones now ship in
  the package, an unservable custom phrase degrades **loudly** (with a one-click
  fix) instead of failing silently, and onboarding verifies the microphone
  level and the spoken wake word before marking setup complete.

## [1.0.0] — 2026-07-03

First **public** release of Personal Jarvis — a voice-driven meta-orchestrator
that turns one spoken request into a fleet of self-checking AI agents.

### Highlights

- **Voice-first pipeline** — wake word → speech-to-text → multi-provider Brain →
  text-to-speech, fully streaming, with honest, language-aware readbacks
  (`de` / `en` / `es`).
- **Provider-agnostic by design** — every tier (router, ack, STT, TTS, worker,
  critic) degrades or crosses provider families on a missing or dead key. No
  single provider is load-bearing, and credentials are managed entirely in-app.
- **Cross-platform core** — the base install boots on a headless
  `python:3.11-slim` VPS; Windows-desktop and local-voice features live behind
  optional extras.
- **Jarvis-Agents mission system** — isolated `git worktree` workers with a
  self-healing critic loop.
- **Plugin marketplace** (OAuth + MCP) and a cross-platform control CLI
  (`jarvisctl`).
- **In-app "Update available" button** — managed desktop installs get a one-click
  "Update Now" control in the top bar when a newer version ships.

### Fixed

- **Desktop app could hang forever on "Getting ready to listen".** The startup
  banner and the top-left voice status cleared only when the speech pipeline
  published its one-shot ready signal. If pipeline construction crashed or an
  un-timed model load wedged warm-up, that signal never fired and the UI stayed
  in "starting up" indefinitely — even though typing already worked. Added two
  fail-safes: the pipeline-construction crash handler now publishes an honest
  degraded-ready signal so the UI is released immediately, and a
  pipeline-independent watchdog in the web server force-releases the UI after a
  generous deadline. The banner can no longer stick forever.
- **Local "Faster-Whisper" appeared as a ready STT provider even when not
  installed.** The provider list never checked whether the local-voice extra was
  present, so the card always showed as configured on a base install, and its
  model dropdown listed all Whisper checkpoints regardless of what was
  downloaded. Local Faster-Whisper has been removed as a user-selectable
  speech-to-text provider; cloud STT (Groq / OpenAI / OpenRouter) is the
  supported dictation path. The wake word (which uses its own local Whisper) and
  the key-free STT resilience fallback are unaffected.
- Declared `click` as an explicit dependency so the `jarvisctl` CLI imports on a
  clean install (it no longer arrives transitively via `typer`), restoring a
  green CI.
- Restored `TRADEMARK.md` to the published tree and removed a dead documentation
  link from the README.
- Stopped defaulting the archival store to the removed `chroma` backend.

### Changed

- `pyproject` metadata for the public release: English, cross-platform
  description and a `[project.urls]` block.

---

## [v1.0.0-board] — 2026-04-25

First consolidated release of the **Jarvis Board**: Phase A through D.

### Added — Phase A (Personal Dashboard)

- `jarvis/board/aggregator.py`: `BoardAggregator` parses FlightRecorder
  JSONL from `data/flight_recorder/`, groups it per day, writes
  `daily_stats` + `personal_records` into `data/board/personal.db`.
- `jarvis/board/store.py`: `BoardStore` as a read-only query facade for the API.
- `jarvis/ui/web/board_routes.py`: GET `/api/board/personal/{summary,
  heatmap,tools,records}` + POST `/refresh`.
- Frontend: `BoardView` with `<HeatmapGrid>`, `<ToolBarChart>`,
  `<StatsCard>`, `<PersonalRecordsList>`. React-Query polling 30 s.
- `recharts` as a new frontend dependency.

### Added — Phase B (Achievements + AI-Bio)

- 10 `AchievementSpec`s in `jarvis/board/achievements.py`: 7 Mastery
  (`first_mcp`, `tool_dabbler/journeyman/master`, `triple_combo`,
  `sub_jarvis_summoner`, `ten_x_engineer`) + 3 Reflection (`centennial`,
  `kilo_club`, `one_year_with_jarvis`).
- `AchievementEvaluator` as an `EventBus` subscriber. Idempotent via
  `INSERT OR IGNORE` on `achievements.id`.
- `AchievementUnlocked` event in `jarvis/core/events.py`.
- `BioGenerator` with `BrainLike` protocol. Anti-cliché test gate against
  12 forbidden words. Brain outage → the old bio is kept.
- `BioScheduler`: asyncio tick 60 s, Sunday 18:00 + master-achievement
  trigger. Date guard via `aggregator_meta`.
- API: GET `/api/board/achievements`, GET `/api/board/bio`, POST
  `/api/board/bio/regenerate`.
- Frontend: `<AIProfileCard>`, `<AchievementGrid>` with a live unlock toast
  via `pushToast` in the WebSocket handler.

### Added — Phase C (Federation Backend)

- New subproject `board-backend/` (FastAPI + SQLAlchemy + SQLite +
  PyNaCl).
- Routes: POST `/api/v1/identity/register` (admin-token), POST
  `/api/v1/sync` (signed), GET `/api/v1/me`, GET `/healthz`.
- Ed25519 crypto + canonical JSON in `crypto.py`.
- Replay protection: `ts_ms` ± 5 min. Constant-time token comparison.
- In-memory rate limiter: 10/min/IP on `/identity/register`.
- Pydantic `extra='forbid'` as the central PII wall.
- Multi-arch Dockerfile (amd64 + arm64) + docker-compose + healthcheck.
- README with three deploy scenarios (Localhost, Raspi, Hetzner+Caddy).
- Local: `jarvis/board/sync.py` as a background push client (60 s).
- New jarvis.toml section `[board.federation]` (default `enabled = false`).

### Added — Phase D (Friends + Federation)

- Backend routes: POST `/pair/{initiate,accept}`, GET `/friends`, PATCH
  `/friends/{pubkey}`, POST `/activities`, POST `/stories`, POST
  `/reactions`, GET `/federation/feed?since=...`, POST
  `/federation/reactions/inbound`, DELETE `/federation/identity/{pubkey}`.
- Tables: `friends`, `pair_tokens` (10-min TTL, single-use),
  `activity_items` (visibility: private/friends/public, optional
  `expires_at` for stories), `reactions` (UNIQUE constraint).
- `interesting_score = reactions × exp(-age_h / 24)` — deterministic,
  hardcoded halflife.
- `FederationPuller` as an asyncio task per friend (offline ≠ blocking).
- `StoriesCleanup` 1 h tick, deletes `expires_at < NOW`.
- Frontend: `<FriendsView>` with tabs "Feed" + "Manage", `<PairDialog>`
  with QR code (`qrcode.react`), `<StoryComposer>` (max 280 chars),
  `<ReactionBar>` (🚀 🧠 🔥, owner-only counts), `<FriendsList>` with
  a per-friend pull-interval stepper.
- Settings page: new section "Backend Connection" (Disconnect, URL,
  copy pubkey).
- Local federation proxy (`jarvis/ui/web/federation_proxy_routes.py`)
  with whitelist paths — the browser frontend does not sign itself, the privkey
  stays in the local backend.

### Added — v1.0 Release Prep

- `tools/board_demo.py` — bootstrap 2 backends + 5 identities + 30 days of activity.
- `tools/board_perf.py` — aggregator + federation-pull benchmark.
- `tools/board_pentest.py` — 19-vector pen test against a live container.
- `docs/jarvis-board/ARCHITECTURE.md` — for backend forkers.
- `docs/jarvis-board/FEDERATION_PROTOCOL.md` — wire-format spec v1.
- `docs/jarvis-board/PERFORMANCE_AUDIT.md` — aggregator 2.94 s / 365d,
  federation pull 17 KB / 10 friends.
- `docs/jarvis-board/SECURITY_AUDIT.md` — 19/19 pen test PASS.
- `docs/jarvis-board/MIGRATION_v1.md` — 4-stage migration for existing users.
- README.md extended with a "Jarvis Board" section.

### Fixed

- `httpx` moved from dev-only to a production dependency in `board-backend/
  pyproject.toml` — `routes/pair.py`, `reactions.py`, `background.py`
  import it on every container start. Bug uncovered during the first
  Phase-D container rebuild for the pen test (Phase D had until then run with
  the Phase-C image).

### Security

- Three independent PII filter layers: aggregator whitelist
  (`export_all_for_federation()`), sync-client whitelist
  (`_build_payload`), server `extra='forbid'` Pydantic wall.
- Ed25519 sigs on all federated routes with re-canonicalize
  (reverse-proxy-resilient).
- Constant-time admin-token comparison + per-IP rate limit.
- 19-vector pen test: auth bypass, replay (past + future),
  tampering, PII leak, malformed body, SQL-injection regression,
  forget-me path mismatch — all PASS.

### Not in Release

- **Phase E (Public Aggregator / Strava-style segments)**: deliberately
  not built. Plan §0 requires ≥ 2 months of Phase-D burn-in first,
  so that anti-cheat mechanisms can be designed evidence-based.
  First Phase-E decision: ~ 2026-06-25.
- **Bundle splitting**: frontend JS is 444 KB gzip (Vite warning).
  Functionally uncritical (<500 ms initial load on modern devices),
  but code splitting for `recharts` + `@tanstack/react-query` is
  a follow-up PR.

---

## [Pre-board] — before 2026-04-24

Phases 0–5 (skeleton, speech, plugin system, risk tier, memory,
harness dispatch, vision/computer-use/admin/async/control/telemetry)
are in the repo, documented in `docs/phase{1a,1c,2,4,5}-*.md` and
ADRs `docs/adr/0001-0008`. This CHANGELOG only starts with the v1.0
Board release — pre-board history is reconstructable via `git log`.
