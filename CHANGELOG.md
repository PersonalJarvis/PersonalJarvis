# Changelog

All notable changes in Personal Jarvis.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning per [SemVer](https://semver.org/).

---

## [Unreleased]

### Added

- **The marketplace is a section of the app now.** Everything the community
  published — plugins, skills and wallpapers — sits behind one sidebar entry
  with one search across all three, instead of being split over a tab, a
  second list and a third screen. Opening an entry shows the publisher, the
  source repository and the actual published files before anything installs,
  and once it lands the app says which section now holds it and takes you
  there. "Open the marketplace" works by voice, in all three languages.

### Fixed

- **"Open the modes" was an unknown section to the assistant.** The character
  screen shipped as a real section but never reached the navigation tool's
  list, so the spoken command went nowhere.

## [1.5.1] — 2026-08-21

### Added

- **The board's centre is a stage, not a reticle.** The mascot stands in the
  middle of the deck now, drawn as a vector measured off the original artwork
  instead of sketched by eye, and framed on the figure rather than on the
  bitmap's empty margin. The bottom row of cards shows its own scale at rest
  instead of five shrunken apologies, and the rest strip no longer carries
  orphans the board never loads.
- **The Jarvis Bar thinks with the deck's sweep.** While a turn is being
  worked on, the bar runs the same sweep the board uses, instead of the old
  orbital core — one motion language across both surfaces.
- **The deck's sweep runs as one transform** instead of sixty redraws a second.
- **A contributor wall on the front page.** The README shows the people who
  have contributed, and it refreshes when a pull request merges instead of
  waiting for the next Monday.

### Fixed

- **macOS stops asking for permissions it has already been given.** The app
  demanded microphone, screen recording and accessibility access that System
  Settings showed as enabled; granting them again changed nothing, and neither
  did a restart. Four causes stacked up. The app rebuilt its own bundle on
  every start whenever an identity probe refused it — the rebuild changes the
  ad-hoc signature, and macOS answers a changed signature by discarding every
  recorded grant, so a probe that failed for a reason no rebuild touches wiped
  the permissions on every single start. A copy in `/Applications` was not
  recognised as this app at all, because only `~/Applications` counted as
  canonical: dragging the app there made that slot look empty, every start
  built a second bundle into it under the same bundle id, and the signature
  change stripped the grants of the copy that was running. The permission
  view applied the same rule, so that same move hid every button and failed
  every runtime check with nothing left to click. And a Screen Recording
  grant given in System Settings stayed invisible, because the preflight macOS
  offers is frozen for the life of the process. Now the running process is
  taken as proof of a valid install wherever it lives, a rebuild that would
  reproduce the identical app is skipped and logged instead of repeated, both
  Applications folders are canonical, and the Screen Recording grant is proved
  by reading a window title only that grant allows.
- **The memory map hands its WebGL context back.** The wiki's 3-D map leaked
  its rendering context when the view was left, and a browser only grants a
  handful of them — enough visits and the map came back as a white canvas with
  a sad face. It now releases the context on the way out and survives losing
  one, and a CI gate fails the build for any scene that does not.
- **The page loads the bundle files that are actually shipped.** Some entry
  chunks lived only in `index.html`, not in the tree, so a fresh clone could
  open onto a blank window.
- **A busy Vertex Live is spoken, not silently retried.** A 1011 resource
  exhaustion used to disappear into a retry loop; it now says what happened.
- **A question is no longer answered with "Erledigt."** In a live call, asking
  something could come back as that one word — a report that a job was
  finished, for a job nobody had given, while the actual answer was thrown
  away seconds later. Two faults stacked up. Gemini and Vertex Live end their
  generation when they hand out a tool call and start a fresh one for the
  spoken answer; the adapter meant to swallow that in-between marker but
  looked for the tool call in the wrong message, so it never once did — 39 of
  those markers in a single day's log, every one of them read as "the provider
  has gone silent". On top of that, the recovery for a silent provider treated
  a finished web search like a finished errand: the search carries no sentence
  to say, only the material for one, and the recovery said "done" and dropped
  the material. Now the adapter waits for the generation that actually speaks,
  and a lookup that came back with something says what it found instead of
  reporting a completed task — with "I have the information but couldn't read
  it out just now" as the honest floor, and "I didn't find anything on that"
  when the search really was empty. A wordless action still reports done, as
  it should.

## [1.5.0] — 2026-08-20

### Added

- **Hybrid tool mode: the live voice model runs the tools itself.** A voice
  turn that needed a tool used to hand off to a second model, and the wait
  was audible — measured over three days, first audio came after 0.9 s when
  the live model answered and after 7.2 s when it delegated. The live model
  now receives the whole tool catalogue, everything except the computer-use
  vehicles, which stay with the Tool Model. The declaration fits a token
  budget (`[voice].realtime_tool_declaration_budget_tokens`, 20k by default)
  and drops whole tool families in a fixed, logged order when it is tight.
  The new `[voice].realtime_tool_mode` ships as `hybrid`; `delegate` and
  `direct` remain for anyone who wants the old behaviour (ADR-0035).
- **A skill is recognised from what you mean, not from a trigger phrase.**
  The skill roster the live model reads now carries each skill's
  `when_to_use` — the sentence that says WHEN it applies, in the words people
  actually use. The first roster capped descriptions at 70 characters and cut
  exactly that away. It now degrades in three steps and never drops a name,
  because a listed name is callable and a folded one is not. A near-match
  reaches the model as a ranked suggestion instead of silence. Both are
  suggestions: no skill can take a turn over that it did not clearly win.
- **The whole orb moves with your voice.** One loop computes the orb's level
  and writes it to a single CSS variable, so the sun, its corona, the rays
  and the ring all move as one thing — the real microphone level while you
  speak, a speech-shaped envelope while Jarvis does, a double heartbeat while
  it thinks. The reticle stays alive at rest, and the board now takes over
  from the start screen with a launch instead of a fade.
- **Grok Build joins the subagents.** The official `grok` CLI is driven over
  a SuperGrok / X Premium+ login, the same shape as Codex, Claude and
  Antigravity. Connect, disconnect, test and set-active live on the Agents
  tab; the xAI API-key card stays separate.
- **Higgsfield joins the marketplace** as a hosted MCP plugin.

### Fixed

- **A failure you hear now names its cause.** "That didn't work" told you
  nothing you could act on. Every spoken failure — a direct tool call, a
  delegated one, a confirmed action that then broke — is built in one place
  and carries the reason the tool reported, in the language of the turn even
  when the rephrasing model is unreachable. A turn that broke in two places
  names both causes.
- **The wake word survives a busy machine.** The confirmation pass now
  streams during the tail instead of after it, audio intake keeps flowing
  while it runs, and the first stage has a CPU budget — on the weak reference
  laptop the wake word was being missed under load (BUG-150). Native Vosk
  calls are serialized, which ends a crash that took the whole process down
  (BUG-151).
- **A skill can actually be started by voice.** A realtime call named
  `run_skill` never reached the `run-skill` tool, so a spoken skill request
  quietly did nothing (BUG-158). A draft authored by voice can now be
  switched on, and your own morning routine wins over the built-in one.
- **Plain conversation stops being handed to the Tool Model.** A greeting or
  a remark was being delegated like an action, which cost seconds and tokens
  for nothing.
- **Music does what you meant.** A play request with no service named goes to
  the one you have connected, "what's playing" answers in hybrid mode
  (BUG-156), an in-flight play is no longer aborted as a stalled turn
  (BUG-157), and a spoken "I'm playing that" that never called a tool is
  recovered instead of counted as done (BUG-154).
- **A dead speaker recovers instead of going quiet.** Native voice is also
  labelled as requested rather than as a fallback (BUG-108, BUG-086), and
  progress lines are spoken in the session's own voice (BUG-155).
- **The Test button in Dictation probes the model that will really run.** It
  used to test a different one, so a broken setup could pass.
- **Picking a file with `@` in the agentic IDE searches the workspace tree**
  instead of stopping at the first five name hits.
- **The app starts lighter.** Every byte of the startup bundle is parsed
  before the window paints, and 212 KB of it belonged to screens the first
  view never shows: the wiki graph library (pulled in by three tooltip
  helpers), the agentic IDE's whole API client (pulled in by two hooks calling
  one endpoint each), and their dependencies. They now travel in the chunks
  that use them. What remains in the startup path is what the mission deck
  actually needs.
- **A window that never received a page is caught too.** The blank-window
  watchdog shipped in 1.5.0 lives in `index.html`, so it can only act once a
  page has arrived — and the window the maintainer kept seeing had none: a
  frame, a title, the ground colour, nothing else. Three routes lead there,
  all logged on one day: the backend's single event loop frozen inside a
  native call, so the socket accepted the navigation and never answered it
  (15 s, 20 s, 42 s, 61 s and 188 s measured, the long ones inside
  PortAudio's stream close during a microphone restart); the backend thread
  dying after the port was already up; and a web view that never navigated
  at all. A guard now runs in the window process, outside the loop it
  watches: it waits out a slow boot, reloads the moment the server answers
  again, and when it cannot fix it, it puts the reason on the window with a
  button — in the browser's language, since the bundle is exactly what is
  missing. That page then polls the server and returns to the app by itself.
  Closing the microphone no longer runs on the event loop either, which
  removes the biggest freeze at its source: the loop now keeps serving while
  a wedged native handle is shut down on a worker thread.
- **A question about your own machine gets an answer, not a refusal.**
  Asking whether the PC is overheating or overloaded matched none of the
  planner's question rules — such a sentence carries no question word and
  no possessive — so the turn was never allowed to read the machine, and
  the execute guard judged the model's shell call by the worst command
  that tool could ever carry. Jarvis answered "I can't run actions right
  now", twice in a row. The planner now knows the machine's own
  vocabulary and the indirect question that hides a lookup inside a
  statement, a call whose arguments only read passes the guard, and a
  turn whose every call was gated says what actually happened instead of
  claiming the assistant is broken.
- **Dictation translation reads like the target language now.** The
  translate pass ran on the same tiny model the formatter uses, which
  punctuates well and translates flatly — measured on real German
  transcripts it dropped whole clauses. A family may now declare a
  translation tier, and the pass uses it: Groq translates on
  `gpt-oss-120b`, Gemini on `gemini-3.7-flash`, OpenRouter on
  `google/gemini-3.7-flash`. A model you pinned yourself still wins.
- **A long dictation is no longer cut off by the wording ceiling.** The
  1200 ms budget was fixed while the work grows with the transcript, so a
  tenth of all wording passes expired on one measured install — and they
  were the long ones. The ceiling now ramps with the transcript length up
  to `[dictation].polish_timeout_max_ms`. Short dictations are unchanged,
  and a healthy provider never reaches either number.
- **Two wording defaults were years out of date.** OpenAI ran on
  `gpt-4.1-nano` and Gemini on a 3.1 lite build; both now use the current
  generation their own catalog serves.
- **A Start-Menu click that cannot boot now says why.** A missing `click`
  (uvicorn's import-time dependency) killed the backend thread, the window
  waited 45 s for `/api/health`, and pythonw swallowed the traceback — so
  Personal Jarvis in the Start recently-used list looked dead. The
  launcher now refuses up front, a dead backend thread no longer waits
  out the timeout, and the window's relaunch command points at
  `PersonalJarvis.exe` like the shortcut.
- **A Gemini Live greeting no longer dies mid-sentence.** Vertex Live fires
  an empty `interrupted` for our own steering text, then speaks the real
  reply. The session used to treat the quiet after that reply's
  `turn_complete` as a confirmed barge-in and cancel the speaker drain —
  full text in the log, half a sentence in the air. The same settle could
  also fire *before* first audio and withhold the whole greeting (BUG-152).
- **Vertex Live native tools work again.** Vertex reports hybrid function
  calls as `default:run_shell`. The bridge treated that as an unknown tool
  and the model said the tools were down (BUG-153).
- **A spoken "I'm playing that" is no longer treated as done.** Hybrid
  voice could announce a playlist (or any other action) without calling
  the tool; the user had to ask again. The session now recovers those
  false completions, music capture rematches to the connected service,
  and an in-flight native tool no longer looks like silence (BUG-154).
- **The Jarvis Bar keeps thinking while the Tool Model works.** After the
  short "I'll play that" ack it used to look ready even though YouTube
  Music was still running. Progress speech now returns to thinking until
  the result is spoken.
- **A window that shows nothing now says why, and heals itself.** Rebuilding
  the frontend while the desktop window is open could leave it empty — the
  ground colour and nothing else, until the app was killed. The single
  automatic reload the app spends on a missing chunk hands the failure to a
  view's error card, and a failure above that has no card to land in; the
  bundle watch that would have picked up the fresh build lives in the bundle
  that is not running. `index.html` now carries a watchdog that runs whatever
  the bundle does: an emptied page reloads once within seconds, a splash that
  never becomes an app after twenty, and a second failure paints what went
  wrong plus a Reload button instead of staying dark. The guard is released as
  soon as the app is up, so a later rebuild in the same session still heals.
- **A granted macOS permission stops reading as missing.** Every start warned
  that permissions were off, and the pane the warning linked to showed them
  on. Three things kept that loop alive. Opening System Settings for Screen
  Recording, Accessibility or Input Monitoring flagged a pending restart even
  when nothing there needed changing, and nothing but quitting the app ever
  cleared it — while it stood, Computer-Use and the global hotkeys were really
  switched off with every grant in place. "Ask again", which clears this app's
  own record so macOS asks once more, appeared only for a permission macOS
  reported as denied; the two probes that strand most often report a lost
  grant as plain "not granted", so the one way out was invisible, and the
  app-wide banner carried no such button at all. And a request macOS silently
  swallowed still promised that a restart would help. A pending restart now
  dies the moment a probe sees the grant, the reset is offered wherever it can
  help and explained in words, and a swallowed request says so. Underneath,
  the app is only ad-hoc signed, so any rebuild of its bundle makes it a
  stranger to macOS and drops every grant: that rebuild now logs why it
  happened, says in the permissions view why it is asking again, and — when
  rebuilds recur — no longer wipes the permissions a second time before you
  have answered the first (BUG-159).
- **The deck opens on the board, and the waiting screen is gone.** Starting
  the app left you on a standby ring under a big "Say 'Hey Nova'", with
  nothing to look at until you spoke. That screen has been cut. The start is
  now the boot sequence and its launch: the four gates light as they turn
  true, and a blink later the SAME hand-off a spoken word triggers hands over
  to the board — orb travel, shockwave, instruments assembling, the scan.
  Speaking, the hotkey or a press on the orb still gets there sooner, and an
  install whose voice stack never reports (no microphone, voice off, a
  headless box) is no longer held on the start screen: with the link up the
  board opens anyway. The wake phrase still greets you — on the board's own
  headline, where it does not block the view.

### Removed

- **`[voice].background_heavy_turns` is gone from the config.** It has been
  declared since 1.4.0 and read nowhere — the classic-pipeline half of
  ADR-0034 is not built yet, so setting it changed nothing. It returns with
  the change that actually reads it. Nothing to do if you never set it.

## [1.4.0] — 2026-08-18

This release turns the front page into a mission deck, gives the assistant a
shelf of characters, and stops making you wait: a heavy turn answers within
seconds, you keep talking while the tool model works, and speaking interrupts
thinking. Music plays by voice on Spotify or YouTube Music, Google Cloud
Vertex AI is a provider family of its own, the marketplace grew a community
registry with install-by-name, and your own skills actually fire. Underneath,
a long list of voice, realtime, Agentic IDE and launcher fixes.

### Added

- **A mission deck as the front page — "what is going on" on one surface, with
  the classic chat one click away.** Opening the app used to show an empty
  chat: a consequence, not a state. The deck shows every section on the left
  with the live ones lit, what the assistant is doing right now on the right,
  and the Jarvis orb in the middle; a header switch moves between deck and
  classic view and the choice is remembered. Every figure comes from a payload
  the bus already publishes; nothing is estimated.
  - **The orb is the Jarvis orb itself,** cut out of the hero artwork. It
    breathes with the voice state (reduced motion gets the still picture) and
    is ringed by one arc per running reasoning step. Clicking it does what the
    wake phrase does: it starts or ends the conversation.
  - **One card per signal, each reading its section's own data:** a LOG
    terminal (one line per thing the assistant heard, thought, did and said,
    with clock time and duration); a RESPONSE instrument (hear → think → act →
    speak, a stopwatch, first-token and first-audio marks); runs; outputs; a
    coding-workspace crew roster (one row per agent — a row jumps INTO that
    terminal); terminals; the wiki as the same 3D memory map the Wiki section
    draws; computer use; screen capture; API this session (tokens and cost per
    model); a live word counter.
  - **The pictures the deck shows.** Screen Context stays "one capture, then
    gone"; a mirror keeps AT MOST ONE frame and forgets it after
    `[screen_context].deck_preview_s` seconds (default 120, 0 = off).
    Computer-use frames are served by hash only (`GET /api/deck/cu-frame/`);
    every image response is `no-store`.
  - **A HUD frame language** — corner brackets, chamfered outlines, tick
    rulers, status lamps in every title — with a halo on every hairline in the
    theme's ground colour, so gold lines hold on both appearances over any
    wallpaper. **One icon rail** (`DockRail`) serves the deck and the collapsed
    sidebar, with a live pip where something is going on and the sidebar's own
    signals (API-keys error, plugin reconnect, the Skills → Plugins shortcut).
  - **A boot sequence and a listening ring before the first word.** The deck
    no longer shows nine instruments saying "nothing yet" from the first
    second. Three acts, forward only per session: *boot* — one big ring
    around the orb, the four gates a voice turn needs (link, voice, brain,
    wake) drawing in as each turns true, a console typing one line per gate
    with clock time and measured duration; *standby* — everything up, nobody
    has spoken: a sweep turns only while the wake word is really being
    listened for, the headline names the phrase to say, "Open the board" is
    one press away; *board* — from the first turn (wake word, hotkey, typed
    message, a press on the orb) the ring folds into the orb, the orb travels
    to its place and the instruments power on from the centre outward.
    Nothing on the stage is invented — every gate is a fact the header lamps
    already show, and a fact that never arrives is called absent instead of
    spinning forever. Reduced motion gets the same facts without the movement.

- **Modes — the assistant gets a shelf of characters, one of them active.** A
  mode is how the assistant behaves: the difference between a butler who
  answers in one line and a friend who asks how your day went. Five built-ins
  ship — Assistant, Friend, Coach, Focus and Coding — and picking one applies
  from the next turn in voice AND in chat, no restart. A mode is a LAYER on
  the base persona, never a replacement, so the rules that keep the assistant
  honest survive every character.
  - **A Modes section:** the shelf, and a workshop where you make another one.
    A card opens with the full character text verbatim, the knobs and the
    voice; every card carries an explicit "Use", "Edit" loads any mode into
    the form, a built-in keeps "restore original". "Talk it through" opens a
    real voice interview — the assistant asks what kind of company you want
    and writes the mode itself. The mode in force and the mode you chose are
    shown apart (the payload gains `chosen` next to `active`). In en/de/es.
  - **By voice:** router tools `list_modes`, `switch_mode` and `save_mode` —
    "be my friend for a bit" works. Switching changes tone only and is undone
    in one sentence; saving is ask-tier because it writes a file every future
    turn reads (ADR-0011 amendment).
  - **CLI-first:** `GET /api/modes`, `PUT /api/modes/active`,
    `POST /api/modes`, `DELETE /api/modes/{slug}`,
    `POST /api/modes/{slug}/restore`, and a `jarvis modes` group (list, show,
    activate, create, delete, restore). `[persona] active_mode` is the sticky
    choice; a screen-scoped override lives in memory only.
  - **A mode can name the voice it speaks in.** A friend should not sound like
    a butler: a mode may carry a TTS voice id; an empty one keeps what you
    configured. A realtime session pins its voice when it opens, so there a
    switch takes effect on the next call; a voice the provider does not have
    falls back to the configured one — going silent is not recoverable.

- **Instant acknowledgment: on a heavy turn the first sign of life arrives
  within seconds, and it speaks to the request (ADR-0033).** Turns that go to
  the Tool Model or a sub-agent left the user 5–30 s without a word. A shared
  core now decides at dispatch, from the deterministic turn plan, what kind of
  work starts and how soon to speak: research, screen, mission and connected
  personal lookups immediately; actions and local lookups after a 3 s grace
  and only if the turn is still processing. The line is request-specific
  ("I'm looking up <thing>."), model-composed and accepted only by a
  structural validator — never a stock "on it"; closed de/en/es pools naming
  the KIND of work are the instant fallback. Both engines get it, realtime and
  classic pipeline; when the work outlasts the first line by 8 s, ONE more
  line grounded in the tool actually running ("Still searching."). The chat
  shows the same first line as a muted pre-ack bubble. Switches:
  `[ack_brain].instant_ack` (kill switch),
  `[ack_brain].instant_ack_compose_all` (on).

- **You keep talking while the tool model works (ADR-0034).** A heavy turn no
  longer blocks the conversation in the realtime engine, provider-neutral.
  Parked results never expire: a late answer waits until the session is at
  rest — the 30 s bound that dropped the answer whenever you kept talking is
  gone — and, spoken after other exchanges, its opening ties it back to what
  was asked. The wire is freed the moment you move on: a new turn while an
  earlier order's provider function call is still open answers that call at
  once with a closed "still executing" payload (Gemini Live blocks on function
  responses; NON_BLOCKING is unsupported on Vertex and the 3.1 Live model),
  `[voice].realtime_unblock_pending_tool_calls` (on). "How far are you?" is
  owned by the orchestrator before the planner can read it as a new order.
  Honest scope: the realtime engine ships this now; the classic pipeline's
  half — `[voice].background_heavy_turns` — is declared in the config in this
  release, but its pipeline reader lands with the pipeline change that
  follows; until then a heavy turn on the classic pipeline still waits inline.
  `docs/adr/0034-non-blocking-heavy-turns.md` names the cells covered,
  emulated and degraded.

- **The Thinking pause decides when a turn is taken — in both engines, and
  you may keep talking.** Jarvis no longer takes the turn on every short
  pause; it waits for a clear one, and words spoken after the pause are
  appended to the same request instead of being submitted twice. The
  Settings "Thinking pause" (`speech.vad_silence_ms`) is now ONE value for
  both voice engines, and which lever applies is a transport capability: a
  transport that answers on its own boundary (`gemini-live`, `vertex-live`)
  gets the pause folded into its native turn detection; a transport whose
  responses Jarvis requests itself (`openai-realtime`, local, third-party)
  waits the pause out on the session's own microphone before it asks for the
  answer, and a transcript that lands while you still talk joins the open
  turn — ONE response answers the whole request. On the classic pipeline the
  VAD already waited; new is the hold when the final transcript lands while
  you are speaking again: the text is held and joined with the next
  utterance, so no instant ack talks over the second half.
- **Speaking while Jarvis thinks or works now interrupts him — no command word
  needed.** Barge-in during PLAYBACK always worked; during the silent THINKING
  wait and a running delegated action it did nothing, and a spoken "stop" was
  even answered with "I am still working on it" (BUG-135). A second local
  Silero detector is fed during the silent wait and fires the same `barge_in`
  control the playback path uses; a deterministic check (regex only, de/en/es,
  whole-utterance anchored so "do not stop the music" is inert) then tells a
  stop, a redirect and a continuation apart. A stop cancels every running
  delegate and confirms out loud; a redirect cancels and routes the
  replacement, so Jarvis holds the NEW context, not silence.

- **YouTube Music as a marketplace plugin — music by voice, on the account you
  already pay for.** "Play Radiohead", "play my running playlist", "play my
  liked songs", "pause", "skip", "what song is this", "like this", "add this to
  my chill playlist", "create a playlist called Late night". Google publishes
  no YouTube Music API, so the plugin rides the official **YouTube Data API v3**
  through the **same Google OAuth client** as Gmail, Drive and Calendar — one
  Cloud project, one more API to enable — and never a reverse-engineered
  private client.
  - **Playback happens in YouTube Music, not in Jarvis** — the same model as
    the Spotify plugin. Playing opens a `music.youtube.com` deep link, so
    Premium, recommendations and history stay in the user's own account;
    Jarvis watches the system's media session and says whether playback
    actually started — including the honest "press play once" when the
    browser withholds autoplay.
  - **Pause, resume, next, previous and "what is playing" come from the OS
    media session** — the same channel the keyboard's media keys use: Windows
    through WinRT (`winrt-*` in the `[desktop]` extra), Linux through
    `playerctl`, macOS through `nowplaying-cli`; without the tool the answer
    names the install command instead of a fake success (`docs/os-parity.md`
    P-33).
  - **Honest limits, said out loud.** Google allows 100 searches a day per
    project (repeats are cached, own playlists cost none); "queue next" does
    not exist in YouTube's API and the skill says so. Setup:
    `docs/marketplace/youtube-music-setup.md`.
  - **A background player instead of a browser tab (default).** A small player
    window of its own (a pywebview companion process with a persistent profile
    — log in once, keep Premium) starts minimized, loads each song in place,
    and answers pause / skip / "what is playing" — and, new, **volume by
    voice**, which no OS media session offers. Where the player cannot run
    (headless, no desktop extras) or when Settings → Music says *Browser*, the
    browser path stays.
  - **Two connectors, one domain — a preference instead of a coin toss.**
    With Spotify AND YouTube Music connected, "spiel Musik" went to whichever <!-- i18n-allow: quoted voice request -->
    music skill was registered first. New `[music] preferred_service`
    (Settings → Music, `PUT /api/settings/music`): a named service always wins
    ("on YouTube Music"), else the preference, else on *Automatic* the only
    connected one. ONE resolver feeds both the deterministic skill capture and
    the two tools' descriptions, so the router and the capture never disagree.

- **Spotify by voice — a native plugin, and the "no active device" problem
  solved rather than reported.** Play, pause, skip, back, volume, "what is
  playing". Spotify publishes no MCP server, only the Web API, so this is a
  native REST tool behind the keyring-backed Connect button; risk tier
  `monitor`. The Web API commands Spotify Connect: with one open device the
  command is re-aimed at it instead of failing with `NO_ACTIVE_DEVICE`; with
  several, the assistant asks; with none, it says so and offers to open
  Spotify. Playback control needs Premium — the refusal names Spotify's rule.
  Bring your own OAuth client: Spotify's Development Mode allows five users
  per app, so `oauth_client_family: spotify` makes your own client the
  documented path (PKCE, no client secret); refresh tokens are capped at six
  months, so the card says `provider_limited`. The Client ID field had
  nowhere to save to; a parity test now closes that seam for every OAuth
  family. Setup: `docs/marketplace/spotify-oauth-setup.md`.

- **Google Cloud Vertex AI as a provider family of its own, on every tier.**
  Vertex serves the same Gemini models as Google AI Studio, but bills a Cloud
  project — and until now it was reachable only by accident, through an
  express-mode key pasted into the Gemini field. Vertex now has its own cards
  for the brain, the tool model, voice input, voice output and realtime, and
  is selectable as a subagent; all of them read ONE shared Vertex credential.
  - Two ways in: a Vertex AI express-mode key (`AQ.`), or a Google Cloud
    project with no key at all (`[google].vertex_project`, `vertex_location`,
    optional `service_account_path`, signed with Application Default
    Credentials). Measured live: a Google Cloud API KEY does not work with
    Vertex at all; the cards and docs say so, and an `AIza` key pasted into a
    Vertex field draws a warning.
  - The endpoint is decided by the card, not guessed from the key, and Vertex
    publishes its own Live model ids. Vertex is a distinct credential family
    with no cross-read to the Gemini slots: the two accounts run out of quota
    independently, which makes crossing between them a real fallback.
  - Worth picking for voice: the AI Studio text-to-speech preview model is
    capped at 100 requests per day no matter how you are billed — what made
    the voice go quiet mid-day. A Cloud project has no such cap.
- **Gemini speech-to-text got the card it never had.** Selecting it meant
  hand-editing `jarvis.toml`; both Google recognizers now appear in Voice
  Input with a model picker.

- **Marketplace: a community registry — browse, install and remove plugins,
  skills and wallpapers other people published, by name, from the app, a
  terminal, or by asking.** New third tab in the Plugins view; every card
  carries a "Community — not reviewed" badge with publisher and version.
  Trust model: `docs/marketplace/community-registry.md`.
  - **The consent dialog is the trust boundary:** before anything is fetched
    it shows verbatim where requests and your token would go (hosted MCP URL)
    or which command would run locally (stdio argv). Every registry rule is
    re-enforced client-side by the Agent Plugins v1.0.0 loader, and bundled
    package cards win over community cards, so no community entry can shadow
    a shipped plugin's vocabulary.
  - **Browsing works offline and never touches boot.** The compiled index is
    fetched with short timeouts, validated tolerantly and cached atomically;
    states are honest — fresh, stale copy, unreachable, disabled — and an
    empty `[marketplace].community_index_url` turns the section off.
    `GET /api/marketplace/community`, install/uninstall routes underneath.
  - **Install by name — one route for every kind.**
    `POST /api/marketplace/community/install/{item_id}` resolves the kind from
    the index and answers in ONE shape: what landed, whether it is usable
    right now (a valid skill is; a plugin is not until connected) and what is
    still missing. `jarvis marketplace install <name>` — the line every
    marketplace page prints — works now: it shows what the entry is BEFORE
    fetching, asks once, then states the outcome ("ready to use — no restart
    needed", "on your list, but NOT connected"); piped or `--json`, no prompt
    can block a script. By voice, `marketplace-browse` reads the index so the
    exact name is found, and `marketplace-install` carries the `ask` tier — a
    plugin brings an outside MCP server with it.
  - **Wallpapers are a published kind**, downloaded over https under a size
    ceiling and put through the SAME mill an upload is.
  - **Read an entry before installing it:**
    `GET /api/marketplace/community/{name}/contents` shows a skill's
    instructions, a plugin's two manifests, a wallpaper's picture. The card
    shows the install as a terminal line to copy (skills also offer
    `npx skills add`), mirrored with the storefront.
  - **What came from the marketplace says so, where it landed** — one
    `MarketplaceBadge` in the Installed tab, on a skill's row, and on a
    wallpaper filter chip; the origin lives in a sidecar beside the thing
    itself. An install from the terminal, by voice or from the storefront now
    reaches the open window (`MarketplaceItemInstalled` on the bus; the
    window reloads exactly the lane that changed, no restart).

- **Bring your own skill or plugin — drop a folder onto the window, or import
  it from a path or link.** For months the backend could import a skill and no
  screen reached it; the honest answer to "how do I add my own skill?" was
  "copy a folder and restart".
  - **Skills: an Upload button in the Skills view.** Drop a folder, pick one,
    or hand over a .zip; the server reports what it found — name, the paths
    that will actually be installed, and every blocker at once — and only then
    is there anything to confirm; a skill that trips the safety lint is
    announced as landing in draft BEFORE the install. Routes:
    `POST /api/skills/upload/inspect`, `POST /api/skills/upload`; staging is
    fail-closed. From a terminal, `jarvis skills import <path-or-url>` routes
    local paths to the new `POST /api/skills/import-local`.
  - **A SKILL.md written for another agent reads now.** A file in the open
    Agent Skills format — the one `npx skills add` installs into Claude Code,
    Cursor, Codex and the rest — used to die on a single foreign frontmatter
    key. A second, tolerant reading is tried only after the strict schema
    rejected the file: only descriptive fields are adopted, by whitelist;
    nothing that grants behaviour crosses over, and a foreign `state` can
    only hold a skill back. Every dropped key is listed on the skill.
    `docs/marketplace/portable-skills.md`.
  - **Plugins: drop a folder holding `plugin.json`** and the preview shows the
    catalog card it will produce, most importantly the authentication mode. A
    local upload gets `source: "local"` with its own mark — never the
    "community" badge and the review it implies.

- **Plugin store: search across every field, filter by connection status, and
  the whole card connects.** The search box matched the display name only —
  "payments" found nothing; free text now matches name, description, category,
  catalog id and OAuth client family, and a connection-status filter joins
  the category menu. Clicking anywhere on a card starts the connect flow
  (ONE lock, so two clicks can never race into two OAuth flows); a connected
  card stays inert, because disconnecting remains a deliberate click.

- **Agentic IDE: a split tree with hand-resizable terminals, branded title
  bars, and a typed prompt bar that composes exactly like the spoken one.**
  Splits stay local and dragged sizes persist
  (`POST /api/agentic-ide/layout/weights`); "Even out" measures TERMINALS, so
  one click lands every pane at the same width. The focused pane's call-sign
  wears a filled brand plate (signal-yellow on dark panes, gold on light
  ones), and all title-bar ink is keyed to the PANE's own ground, so a light
  pane inside a dark app stays readable. Typing into the prompt bar sends one
  request with compose on: Jarvis writes the briefed task with `@file`
  references and types THAT into the pane, and the composer's progress beats
  ride the event bus (`AgenticIdeComposeProgress`), so 10–30 s of real model
  work no longer looks like a wedged spinner.

- **Agent accounts show how much of each subscription is left.** Holding
  several seats of the same coding CLI made the switch possible but left the
  number the choice is made on invisible. Each row now carries its own meters
  — the rolling 5-hour window, the weekly one, and any per-model weekly budget
  the provider scopes separately; readings are live where a provider offers
  them, else what the CLI last wrote to disk, and every block says which. No
  token is ever returned or logged. `GET /api/agent-accounts/usage`.

- **Your own skills actually fire now.** Fourteen days of live telemetry
  showed zero model-initiated `run-skill` calls — the "my skill never fires"
  complaint, measured. Naming a skill captures deterministically (an
  imperative use-verb, the word "skill(s)" and an installed skill's spoken
  name together fire it; a question about a skill is vetoed); the relevance
  layer leaves shadow mode (`[skills].relevance_shadow` defaults to `false`);
  the model sees EVERY active skill (cap 48 instead of 20, user-authored
  first); a clear NARROW match carries the skill's full instructions inline,
  so a fast router model does not turn a hint into a tool round trip.

- **Create a skill by voice — and it is a real skill, not your sentence in a
  template.** "Erstell mir einen neuen Skill: Morgenroutine, jeden Morgen um 6 <!-- i18n-allow: quoted voice request -->
  Uhr E-Mails, Tickets und Kalender vorlesen und dann ein 80er-Klassiker auf <!-- i18n-allow: quoted voice request -->
  YouTube Music" now ends with a written skill: the assistant itself authors
  the whole card — name, the spoken phrase that starts it, a schedule trigger
  (`0 6 * * *`) when you named a time, numbered steps that each name the
  connector actually attached right now, and a spoken answer format — and
  files it as a draft you switch on in the Skills view. New router tool
  `create-skill` (one bounded call, never a background worker), the same
  author behind `POST /api/skills/creator/author` and the new
  `jarvis skills create "<what it should do>" [--name] [--trigger] [--schedule]`.
  The author is a provider LADDER, not one model: your active provider first,
  then the pinned Tool Model, then the API quality tier, then the frontier
  chain — and if nothing can author, the assistant says so and writes nothing.
  The UI's skill-creator dialog and `jarvis skills draft` see the same live
  tool inventory; `draft` gained `--trigger`, `--schedule`, `--language`.
  - **The rest of the lifecycle by voice:** "welche Skills habe ich", <!-- i18n-allow: quoted voice requests -->
    "aktiviere den Skill Morgenroutine", "deaktiviere den Spotify-Skill", "lösch <!-- i18n-allow: quoted voice requests -->
    den Skill Abendroutine" are first-class app commands now (`skills-list` <!-- i18n-allow: quoted voice requests -->
    over a new lean `GET /api/skills/brief`, `skill-enable`, `skill-disable`,
    `skill-delete` — the last one asks first). Enabling the draft the assistant
    just wrote is one sentence away, and it happens only when you ask.
- **The assistant knows its own CLI.** The `cli_jarvisctl` tool now carries
  the complete `jarvisctl` command tree with argument hints, parity-tested
  against the real CLI — no more turns spent reading `--help`.

- **The memory map is a solar system that turns around YOUR page.** The Wiki
  section's 3D map used to turn as one rigid body and read as a flat disc.
  The hub is the sun and it is your own entity page
  (`[memory.wiki.session_rollup].user_entity_slug`); pages sit on soft shells
  by hop count, each turning at its own Kepler speed; the camera looks THROUGH
  the network. Every wiki write hands each page its previous place, so the
  network no longer explodes and re-settles on each change;
  `prefers-reduced-motion` gets the still layout.

- **Run visualization draws a run as an n8n-style workflow.** Every card
  carries a category — trigger, reasoning, command, file, search, web,
  integration, agent, result, deliverable — with its own glyph and per-theme
  hue; parallel workers branch into lanes that merge at the result.
  `?run=<slug>` deep-links a detached window to the run it talks about.

- **A plainly heavy request delegates without a magic word.** Background work
  could only start on a delegation word or a confirmed offer, so "Build me a
  Flask app with a login and a start page" produced an inline answer — never
  work. A second route in the spawn gate now runs beside the vocabulary one:
  the turn must read as a request, name a deliverable, carry no small-scope
  word, and clear a scope threshold — AND the model must have chosen the tool
  itself. `[brain.routing].force_spawn_mode` gains `balanced` between `strict`
  and `permissive` and ships as the default; "build me a website" stays
  inline, add Flask and a start page and it goes.

- **Background work reaches the user.** The Conductor scheduler had been
  running from every boot and writing every result into its own database
  without a line to the bus, the voice or the notifications. Its Runner now
  emits structured facts and the app decides what is said and in which
  language. News means a state change and nothing else: a job that starts
  failing, one that recovers, a first-ever failure; the 288th healthy run is
  silence. The morning briefing is on the clock on a fresh install and follows
  the configured output language instead of hardcoded German.

- **Two plain wallpapers: black and white.** The Wallpaper section now opens
  with four pictures that ship inside the app — the night original, its
  daylight twin, pure black and pure white — under two chips, "Original" and
  "Plain". Black is a dark wallpaper, white a light one, so adopting either
  switches the mode along with the ground; "Default" still returns to the
  original of the mode you are in. The plain grounds are drawn, not
  downloaded — present on a machine with no library and no backend.

### Changed

- **The router prompt shrank from 27.5k to 3k characters, and it now says
  "do Y" instead of "never X".** It made up 48 % of the system prompt on every
  turn and carried 143 prohibition markers against almost no positive
  instruction — the classic recipe for a model that answers instead of acting.
  Gone: rules already enforced in code, rules the persona already owns, and
  four contradictions resolved in favour of the acting side; kept verbatim:
  the decision table, the `spawn_worker` argument format, and every dated
  live-failure rule. The prompt also no longer orders silence — an empty
  reply reaches TTS as nothing, so a spoken command looked ignored.
- **Real questions go to the capable model.** A keyword list decided the model
  tier: fifteen of sixteen substantive requests took the cheap model ("list
  the tradeoffs between …" matched a bare "list"). The capable model is the
  default now and no keyword can demote; only a whole-utterance greeting,
  thanks, farewell or clock question and a verb-first command of at most six
  words stay fast — where both tiers answer identically.
- **The model is told which tools it actually has.** The prompt rendered 25
  names from a static seed list under "complete list — no others exist";
  against a real surface of 84 attached tools not one was a callable name —
  one reason it said "I don't have a tool for that" while the tool sat in its
  surface. The block is now rendered from the live surface, and the delegated
  voice budget grows from 6 rounds / 20 s to 12 / 45 s.
- **Agentic IDE: the brief writer answers soonest, and a readback that names
  the wrong pane is caught and recorded.** A spoken "T2, do a deep dive on …"
  reached the pane 26 s after the sentence ended, 19 s of it the coding CLI's
  own process start. Under `auto` the order is now Tool Model → API tier →
  coding CLI (~5 s warm); a hedge that dies is replaced by the next rung
  instead of a 90 s fallback (BUG-145). The composer no longer spends 10–30 s
  of reasoning on a spec the receiving agent opens anyway: thinking is off,
  the brief is lean, and a terminal brief is written in 1–5 s. Separately, twice the live model
  reported the pane the USER asked about instead of the one the trusted result
  opened; a mismatch is now published as a recoverable
  `readback_identifier_swap`. Observation-only for now: how often it would
  fire is not yet measured.
- **Gemini worker runs are recorded as structured streams.** The Gemini CLI
  worker ran in text mode, so the Critic graded blind and the Visualization
  drew every Gemini run as bare Request → Result. It now uses stream-json.
- **The marketplace storefront lists only registry-published entries.** Until
  2026-08-16 it listed all 21 built-in connectors as store entries under an
  "Official" tick — and none was installable. The rule is in the contract now
  (`docs/agent-contract.md` §3) and binds app, registry and storefront alike:
  a listing is a registry-published entry with an Agent Plugins 1.0.0
  manifest; built-ins belong in the "built in" wall and reach the store only
  through a registry PR like anyone else's; "Official" names an author, never
  a review — every entry keeps its "Not human-reviewed" mark, ours included.
- **Provider & mode parity is a binding rule of the contract.** Every feature
  — and every plan before it — targets the full provider × mode × OS matrix
  from the first sentence: every Brain/Tool-Model family incl. local, every
  realtime transport, every STT/TTS/Vision/Wake provider, realtime engine AND
  classic pipeline, chat, channels, CLI. A provider-specific mechanism is one
  declared capability; the rest get the generic path, an emulation, or honest
  degradation (`docs/agent-contract.md` §3).

### Fixed

- **Windows: the app is findable again — Start-Menu entry, Desktop shortcut,
  and a launcher that leaves the Store Python's container (BUG-138).** The
  Start-Menu shortcut pointed at `pythonw.exe`, and Windows never lists a
  shortcut whose target is a generic host as an app — it now points at a
  branded `PersonalJarvis.exe` inside the venv. A Store Python runs inside an
  MSIX container, so every write to `%APPDATA%` was diverted into a private
  per-package tree while the shell saw an empty folder — the launcher is now
  published out of the container through a helper with no package identity.
  A Desktop shortcut is installed, repaired and removed alongside the
  Start-Menu entry, and only a real window run claims the shortcut. Same
  defect shape on Linux (`update-desktop-database`) and macOS (`lsregister`),
  fixed in the same change (`docs/os-parity.md`).
- **A start that cannot happen now says so, on every OS — and bare `jarvis`
  opens the desktop app it always promised.** Under `pythonw`, a macOS `.app`
  or a Linux `.desktop`, a boot-killing `ModuleNotFoundError` went nowhere.
  The one import that decides it (pywebview) is checked right after argument
  parsing (not in `--headless`), logged, and shown in a native dialog when
  stderr is the null device — a message box on Windows, `osascript` on macOS,
  `zenity` or `kdialog` on Linux. Separately, `jarvis` with no arguments ran
  a tray icon with no backend and no window, blocking forever; it now uses
  the same launcher as `run.bat`, and the tray loop survives behind `--tray`.
- **One spoken order stays one turn — even past a thinking pause, even a long
  one.** A realtime provider commits the turn on ITS server VAD; live
  2026-08-13, ONE order was chopped into three turns at ordinary hesitation
  pauses and every fragment dispatched an executor — the same coding pane was
  briefed twice with a quarter of the sentence (BUG-131, BUG-137). The
  microphone is now the evidence: voiced input frames stamp a local "the user
  is talking" state, never consulted while Jarvis speaks. A boundary that
  lands mid-voice appends to the turn instead of splitting it; a delegate
  holds its dispatch until the mic goes quiet; a background result from an
  ALREADY ENDED call is never injected into a sentence. No added latency on
  an ordinary utterance. New session knob `end_of_speech_sensitivity`
  (default `low`) asks the provider to be patient too.
- **A cough no longer ends the answer, and the realtime role stops flipping.**
  Every interrupted edge cancelled the running answer — and Gemini reports
  background noise the same way, so a cough mid-answer left half a statement.
  The final input transcript now does the actual cut, because a real barge-in
  produces words and a cough does not (a settle backstop commits after a
  second of silence). The tool directive is one standing role plus one short
  per-turn line instead of three full-text versions per turn. Stated, not
  fixed: Gemini's `start_of_speech_sensitivity` is still unconfigured.
- **A delegate reply and a readback are each spoken once (BUG-143, BUG-148),
  and Vertex Live gets its emergency voice.** On Gemini Live one Jarvis turn
  carried two turn-ending inputs on the server and both were answered back to
  back ("Ich habe work geöffnet: T eins." and then "…: T1."). The session <!-- i18n-allow: quoted voice output -->
  cannot cancel that second generation, so it refuses to play it: a bounded
  guard discards a generation that begins with no open turn, no new
  transcript, no local voice and no injection. The guard is armed after the
  desktop's speaker queue has drained — before, a long readback was spoken
  twice and three grounded answers in a row were then never heard. And
  `vertex-live` now maps to Vertex TTS for the no-audio fallback, where the
  fallback used to stay text-only.
- **Realtime: "Let me check that…" is no longer cancelled mid-sentence.** The
  unbacked-promise detector ran on every streaming delta, and its "fewer than
  24 characters follow the commitment" test is true by construction on a
  growing prefix. The judgement now happens at `turn_complete`, with a
  bounded backstop after 0.35 s of provider silence. Cost: an answer opening
  with a commitment is held for two or three deltas (roughly 100–500 ms).
- **Gemini Live: per-turn directives reach the model instead of being thrown
  away.** `update_session` discarded every per-turn instruction — the turn
  directive, the language pin after a switch — while behaving as if it had
  been delivered, so the model answered from a frozen prompt. Changed
  directives now travel as a small out-of-band text turn (the channel BUG-104
  established), delta only, on an open user turn; a prompted retry speaks
  instead of staying silent until the 20 s stall watchdog.
- **Vertex: a Google Cloud project boots as configured, a Live session opens
  in about a second instead of six to twelve, chat and Live get different
  regions, and the key field saves (BUG-139..141).** The pre-boot key check
  dead-listed `vertex` on every boot ("no key") because the project path
  stores no key — Application Default Credentials sign; the keyless rescue
  now consults the same credential-probe table every card uses. Credentials
  and the TLS trust store are resolved once per process instead of inside
  every Live handshake — session open 5.7–12.2 s → 1.1–1.5 s — and the Live
  adapter declares the 20 s handshake budget it needs. Measured live, `global`
  opens NO Live session while named regions lag a generation on chat: new
  `[google].vertex_realtime_location`; only a `global` `vertex_location`
  falls back to a named region, with a warning, because moving where audio is
  processed is a data-residency decision. On the cards, "Unknown secret key:
  realtime_vertex_api_key" — the Vertex key slots existed everywhere a
  credential is READ and nowhere it is WRITTEN; and the key-format hint no
  longer calls an `AIza` key a "Google AI Studio key" — the label is
  endpoint-neutral, and a correctly pasted Vertex key gets its green
  confirmation instead of silence.
- **"Look here, close that window" now does both — the tool surface is
  narrowed, not emptied.** Three gates removed almost everything the model
  could act with: a smalltalk turn kept one tool, a screen-image turn got an
  empty dict, and any turn whose verb missed a regex lost computer_use, both
  spawn tools and all five write tools — which is why "play music" had no
  vehicle. Each gate now hides a specific tool class on positive evidence
  (screen turns still cannot start a background agent from injected on-screen
  text); an image turn goes from 0 tools to 5. German separable verbs count
  as operations now: "Mach das Fenster zu" put its particle at the end and <!-- i18n-allow: quoted voice request -->
  registered as a pure look.
- **A refusal checks the tools actually attached, not just the capability
  registry.** The evidence gate, the unsupported-intent check and the
  local-action gate consulted the registry alone — a cache that lags reality
  — so a freshly connected plugin, CLI or MCP server was callable and
  invisible at once: "I have no calendar access" while a calendar tool sat in
  the tool surface. All three now consult the live tool surface as the last
  step before speaking; coverage is decided on registered tool NAMES, and a
  Telegram tool does not answer a WhatsApp request.
- **Three guards no longer block on everyday German words, and a question
  opener no longer cancels the command behind it.** A turn opening with
  "warum" or "wie" blocked every side-effect tool, so "Warum ist Spotify <!-- i18n-allow: quoted voice request -->
  nicht offen, mach es auf" was guaranteed to do nothing; the guard stands <!-- i18n-allow: quoted voice request -->
  down when a real imperative follows. The meta-debug keyword list held
  "Fehler", "Log", "Provider", "Bug" and "Phrase" and ended the turn with a <!-- i18n-allow: quoted voice request -->
  canned line even after another tool had run; those words count only
  alongside a reference to the assistant itself. A research verb blocked
  every `cli_*` and MCP tool, so a question about your own cloud costs could
  not read your own data; it stands down on a true possessive.
- **A promise no longer swallows the answer it frames.** An answer that opened
  with "Ich schaue kurz." and closed with "I'll tell you when that changes" <!-- i18n-allow: quoted voice output -->
  matched the commitment-and-defer check on both ends, and everything between
  them — the actual answer — was replaced by a canned phrase. The text is
  split into clauses and each is classified by structure, the same in German,
  English and Spanish: with substance present the promise clauses are
  stripped and the answer kept. Two siblings: a streamed answer opening with
  "[" was dropped whole (a reply starting with a markdown link was lost); and
  the unverified-answer backstop deleted correct answers — it now replaces
  only a concrete claim the model could not have known.
- **A tool call the model wrote inside prose runs, and its envelope is
  stripped whole.** The recovery path required the ENTIRE response to be
  valid JSON, so a model that wrote "Ich öffne Spotify." in front of its <!-- i18n-allow: quoted voice output -->
  tool call executed nothing. Envelopes are found by scanning for balanced
  braces; four gates still fail closed (an explicit envelope shape, an odd
  fence count means truncated, a quote against the envelope means quoted,
  short prose free of example vocabulary). No bare "}" is spoken.
- **Six voice filters no longer destroy correct answers, and a scrub can no
  longer speak an error mid-answer.** `scrub_for_voice` is the last thing that
  touches the model's words, and six of its patterns ate the answer: a single
  shell token replaced the WHOLE text with the generic error phrase (every
  PowerShell how-to came out as an error); the narration stripper left
  nothing; the tool-args pattern deleted up to 400 characters after the words
  "action is"; the fn-call pattern matched ANY word followed by parentheses;
  the jargon list deleted "Provider" and "MCP"; every URL was deleted. Each
  keeps its protection with a narrower match. The streaming path filtered per
  sentence, so a short sentence that lost its only noun was spoken as an error
  inside a healthy answer — it is dropped now, and the fallback is spoken
  exactly once, only when the whole turn is empty. The sentence splitter no
  longer breaks "Am 1. Januar" into "Am eins." and "Januar", nor "z. B.". <!-- i18n-allow: quoted voice output -->
- **Dates, versions and IP addresses are no longer read as one giant number.**
  The number speller stripped the thousands separator before spelling, and in
  German and Spanish that separator is the dot, so "17.08.2026" came out as
  seventeen million and change and "Python 3.11" as three hundred eleven — on
  every voice turn. Dotted tokens are classified first (date, IPv4, version,
  thousands/decimal): dates are spoken as dates in each language, versions and
  addresses group by group. English dates reached TTS as raw digits and were
  never spelled at all; fixed in passing.
- **One language resolver decides the turn's language, as the contract says.**
  Three layers re-derived it with their own German-or-English guess, and each
  lost user-facing output: the ack generator scored a short German line as
  English and DROPPED it; the evidence gate refused in a sniffed language and
  knew no Spanish; the Cartesia and Inworld voices picked German on three bare
  articles. Now the ack asks the canonical validator, the brain passes the
  resolved language to the gate, the voice plugins take the caller's language
  code and warn instead of guessing, and the turn resolves its language once.
- **No more waiting a minute for an approval nobody can give.** An ask-tier
  tool blocked for sixty seconds on a confirmation and counted the silence as
  a refusal; only the voice paths ever passed the flag that lets a human
  answer, so the CLI, the REST API, the chat surface and every unattended
  runner walked into that stall and came out denied. A call now declares
  which approval channel it actually has, in the config snapshot the surface
  builds in code — never in the arguments the model produces. Desktop chat
  uses the two-turn confirmation the voice path already had; an unattended
  runner returns immediately with "approval was impossible", not "the tool
  was refused". Timeout and denial are distinct outcomes now.
- **Scheduled workflows can call tools and drive the desktop.** The workflow
  runner was built with no tool registry and no harness manager attached, so
  every `harness_dispatch` or `tool_call` step raised "No HarnessManager
  available" by construction. Tools now arrive through a live view onto the
  brain. Known and deliberately left: an ask-tier tool in an unattended cron
  workflow blocks on approval and times out honestly — silent auto-approval is
  a safety decision, not a wiring fix.
- **Background work that dies says so out loud, and a finished mission never
  goes unannounced.** `spawn_worker` created the mission, found no
  Kontrollierer and returned after a bare log line (the mission stayed
  PENDING forever); a failed cron trigger, a crashed scheduler tick and a
  failed workflow step were log-only. All announce through the readback path
  scheduled tasks already use, so "exit 1" becomes a plain sentence; one
  shared `FailureAnnouncer` speaks the first failure at once and keeps a
  repeat silent for an hour. A mission whose summary did not survive the voice
  filter used to fall silent; the readback retries with the summary-less
  phrase.
- **Computer use: no acknowledgment for a screen action the machine cannot
  run, the 120 s mission guillotine is lifted, a mission may wait for a slow
  screen, and the queue wait is no longer charged to its budget.** The
  deterministic fast path spoke a commitment without checking that
  computer-use is wired at all; it now runs the preflight before the
  acknowledgment and refuses honestly with a `cu_not_wired` phrase in all
  three locales. A desktop mission was cut off after a hardcoded 120 seconds
  while the step budget allowed 100 steps; the ceiling is
  `[computer_use].mission_timeout_s` now, default 600. Waiting for a page or
  an installer looked exactly like being stuck, so the model re-clicked a
  control that was already working — which IS what trips the dead-end guard;
  the model is now told that waiting is work, bounded (six consecutive waits
  with no visible change, two minutes per mission). A mission queued behind an
  active one used to burn its whole budget on the desktop lock; the work
  clock now starts when the lock is actually held.
- **Agentic IDE: fleet briefs by voice land on the panes you named in the size
  you said, hanging up ends a pane's brief, the folder step reads like `cd`,
  and panes are always shown.**
  - Six deterministic repairs to fleet briefs, each from a live 2026-08-12/13
    call: "prompt the five claudes to X, the two codexes to Y" yields a per-CLI
    task map instead of every pane receiving the whole enumeration (BUG-132);
    counts in the TASK half of a fleet request never add panes — "open two
    terminals and the two should hunt bugs, one takes macOS and one takes
    Linux" opened FOUR billed panes for a spoken "two" (BUG-134); a spoken
    self-correction no longer opens the RETRACTED fleet (BUG-130); a garbled
    "Claudes" earns the "did you mean Claude Code?" question instead of two
    silently inherited panes.
  - Composing a brief takes 16–30 s while a delegated voice turn is
    force-answered after 20 s, so one order produced four deliveries and a
    pane typed into 20 s after the user had hung up (BUG-136). Voice teardown
    abandons briefs still being written; REST and CLI keep the detach.
  - A split follows the subscription switch unless its anchor's seat was
    chosen deliberately: only a pinned seat propagates.
  - The folder step (BUG-144): a typed "cd project" went through verbatim to a
    "Not a folder" review step; a leading `cd` and quotes are dropped, a
    relative path resolves against the folder on screen, and a typed path
    becomes the selection only after the backend confirmed it is a folder.
    "Browse" opened BEHIND the maximised app; it opens in front now. A dropped
    folder used to be searched for by NAME; inside the desktop shell the host
    now reports the real path (WebView2 verified; `docs/os-parity.md` P-34,
    macOS/Linux pending).
  - Panes are always shown, and the wizard advises from ten terminals instead
    of asking (BUG-146). Six panes on an ordinary window measured 56 columns
    each and every one was covered by a "needs about 60 columns" card; both
    that card and the wizard's block were built against a repaint bug
    measured at ~13 columns but fired at three to four times that width. Both
    are gone; the terminal is exactly as wide as its tile, and the thresholds
    (10 terminals, 40 columns) are one muted sentence of advice. Trade-off
    stated: the ~13-column blank pane is announced beforehand, not prevented.
- **The mode you picked is read back, and coding mode stays scoped to the
  Agentic IDE.** The configured-slug reader imported a `get_config` that does
  not exist and a blanket `except` turned the ImportError into "the default
  mode", so every switch was written to `jarvis.toml` and never read: the
  assistant kept answering as Assistant. It reads through `load_config()` now,
  and a built-in overlaid by your own file is marked `edited`. Opening the IDE
  used to switch coding mode on and nothing switched it off, so the assistant
  stayed in coding mode in Chats and by voice until restart; leaving the
  section hands the mode back — only a mode this SCREEN switched on — through
  an in-memory override the registry derives from its own state at every
  transition, so it can never overwrite the mode you chose.
- **A request to CREATE a skill is no longer captured by a skill it merely
  mentions.** Voice session 2026-08-18: "… einen neuen Skill erstellst … ein <!-- i18n-allow: quoted voice request -->
  Lied mit YouTube Music …" was taken by the YouTube Music skill because its <!-- i18n-allow: quoted voice request -->
  brand trigger matched the words INSIDE the description, the model spent the
  whole tool budget reading `jarvisctl --help`, and the answer was "Das hat
  gerade nicht geklappt." A deterministic authoring resolver <!-- i18n-allow: quoted voice request -->
  (`jarvis/skills/authoring_request.py`) now decides "the user wants a NEW
  skill" before any brand trigger runs — in every spoken conjugation ("Skill
  erstellst", "skill zu erstellen", "create a skill", "crea un skill") — and <!-- i18n-allow: quoted voice requests -->
  keeps force-spawn and the evidence gate out of such a turn. The
  `skill-creator` builtin's own card is a short "call create-skill once" now;
  Anthropic's long guide moved to `references/anthropic-skill-creator.md`.
  The same rule now covers every word the product uses for "a skill" —
  routine ("Morgenroutine"), automation, workflow, Ablauf — and lifecycle <!-- i18n-allow: quoted voice requests -->
  requests: "deaktiviere den YouTube-Music-Skill" no longer RUNS the music <!-- i18n-allow: quoted voice request -->
  skill.
- **The wake word stops firing on random words and no longer goes deaf
  mid-sentence** (BUG-133). Four stacked causes in the Vosk keyword spotter:
  the acoustic competition re-scored the whole 3 s ring instead of the
  span-trimmed audio, a leading-isolation gate hard-rejected any wake with
  speech shortly before it, the verify re-score pooled phrase words from
  anywhere in the decode, and a stage-1 hit during backoff died behind a
  stale candidate. New `scripts/vosk_wake_bench.py` replays synthesized de+en
  voices through the real detector: false fires 10/48 → 0/48.
- **Dictation's polish pass may no longer cut the verb out of a sentence.**
  Measured on 68 live polished dictations, 33 had lost at least one word and
  exactly one was ever rejected. Prompt v7 states the floor (a filler is a
  SOUND; a verb, auxiliary, modal or negation is corrected in place, never
  removed) and a new positional `lost_verb` drift guard enforces it.
- **Push-to-talk heals itself when the key-up edge is lost.** A lost release
  (focus change, UAC prompt, RDP reconnect) left the recording pill open for
  the full 60 s max-hold while every later press was swallowed as key-repeat.
  A press arriving past the repeat grace window is treated as the release
  that never arrived and submits what was held.
- **Marketplace security: a community plugin no longer inherits your keys, an
  https-only download stays https-only past a redirect, launcher arguments
  cannot walk around the allowlist, and a skill import refuses path-shaped
  names.** A connected stdio plugin was started with a copy of the whole
  environment — where `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`
  and every other exported credential live; an untrusted server now starts
  with an allowlisted environment plus its own declared token (not covered,
  and written into the trust model: the delegated worker path hands the
  server definition to the `claude` CLI). The https guard now travels with
  the request past every redirect. The stdio rules checked exactly the first
  word of `npx`, `uvx` or `docker`, while each has options that hand the
  command line back to the publisher; arguments are now an allowlist per
  launcher and the package must be a pinned name. And a skill's frontmatter
  `name` becomes its install folder and was only checked for non-emptiness,
  so `../../evil` was an arbitrary-file-write primitive; both import routes
  enforce the slug charset and containment.
- **Models the static price table never listed are priced, and the Live
  socket's real model is metered (BUG-142).** The deck's "API this session"
  card showed 272k tokens as $0: `gemini-3.7-flash` and the Vertex Live
  default were absent from the table, and the realtime session reported an
  empty model id whenever the card pinned none. Rates now go static table →
  cached provider feed (OpenRouter pricing rides on `ModelInfo.pricing`) → $0
  with a warning; realtime sessions expose the id they really connected. The
  table gains `gemini-3.7-flash` and `gemini-live-2.5-flash-native-audio` and
  corrects `gemini-2.5-flash`/`-lite` and `gemini-3.6-flash` to the vendor's
  list price (verified 2026-08-18).
- **The desktop keeps its browser profile across restarts, and light mode has
  an original wallpaper of its own.** pywebview starts in private mode by
  default: the WebView2 profile lived in a fresh temp folder per launch, and
  everything the frontend keeps in localStorage died with the process — the
  chosen wallpaper, the deck/classic surface, pane sizes, the theme cache —
  which is how a restart landed on light chrome over dark artwork. The
  profile now lives in `data/webview` per checkout (`docs/os-parity.md` P-35:
  only WebView2 has been run live). And a mode WITHOUT a pick fell back to
  the one bundled night scene, so a switch to light put light chrome on a
  scene where nothing could be read; each mode now falls back to the picture
  authored for it.
- **Deck: the signal colours read on paper, the log shows what the live
  session heard, and the crew roster stacks when narrow.** The deck was built
  on the dark stage only — tints that glow on black and vanish on ivory; every
  tint is a pair now. The realtime session never publishes `TranscriptFinal` —
  what you said arrives as `TranscriptionUpdate` flagged final — so a live
  conversation showed every SAY, THINK and DONE line and not one word of your
  own; the log and the word counters read both events.
- **The background music player: no per-turn config parse, an honest request
  deadline, a player that always dies.** The music tools read the preferred
  service from a briefly cached read instead of parsing `jarvis.toml` on every
  router turn; the player client's request wait uses one fixed monotonic
  deadline; the host tears its window down in a `finally`, so a read fault
  can no longer leave a ghost player, and the free-form eval command is gone
  from the protocol.

### Removed

- **The typed composer left the mission deck.** You speak on the deck, you
  type on the classic surface; the message bar duplicated the chat and cost
  the cards a row.
- **The approval preview in the Agentic IDE's typed prompt bar is retired.**
  Its fallbacks delivered raw typed text into a pane, which is what the
  composing bar exists to rule out; the composed brief is typed directly.
- **The plugin store's "coming soon" strip is gone** — every name on it has a
  real catalog entry.
- **Privacy: two PII scrub manifests are no longer published.** The scrub
  pattern files were tracked in the public repo, so the files defining what
  must never be public were themselves an inventory of it. They are untracked
  and gitignored; the CI docs scan falls back to a generic manifest.

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
