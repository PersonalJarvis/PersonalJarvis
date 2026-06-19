# First-Time Onboarding & Setup Guide — Design Spec

- **Date:** 2026-06-19
- **Status:** Approved for planning (brainstorming complete)
- **Author:** Onboarding working session
- **Topic:** First-time-user experience (FTUE) shown on the first launch of the Personal Jarvis desktop app

---

## 1. Context & Goal

A user who clones or downloads Personal Jarvis from GitHub and launches it for the first
time today lands on a fully functional but unguided app. The only first-run UX is the
`WakeWordOnboardingGate` — a blocking overlay that forces a single wake-word entry.

The goal is a branded, multi-step **first-time setup guide** that welcomes new users,
plays a short intro clip, walks them through the minimum configuration, and gets them to a
working assistant fast. It must run on a freshly cloned install with **no prior
configuration, no API keys, no local GPU, and no guaranteed microphone** — consistent with
the project's cloud-first / cross-platform doctrine.

The guide also carries a **legal layer**: a Terms & Disclaimer acceptance gate, a
trademark-awareness notice with reference links on the wake-word step, and a required
self-certification that the user reviewed them and is responsible (no technical blocking). The wake-word
feature exists specifically because the name "Jarvis" is trademarked (Marvel/Disney) and
cannot ship as a default; users must choose their own activation word.

### Success criteria

1. On first launch the user sees a guided, branded flow rather than the bare app.
2. The flow works end-to-end on a clean clone with zero keys and no microphone (graceful
   degradation, never a dead end).
3. Wake-word selection forces a deliberate choice; the user is shown trademark references,
   must acknowledge they reviewed them and are solely responsible, and may choose any word
   (no technical blocking).
4. The user must accept versioned Terms & Disclaimer before any configuration. Acceptance
   is recorded (version + timestamp).
5. The flow is resumable, idempotent, and never re-shown to already-onboarded installs.
6. A developer can replay the first-run flow without destroying a configured environment.

### Non-goals

- Building the final Remotion intro video (a media slot + animated fallback ships now; the
  rendered clip drops in later without flow changes).
- Re-implementing wake-word / language / API-key / persona logic — the guide reuses the
  existing REST endpoints and settings panels.
- A cloud backend, telemetry, or any data collection by the project. Everything is local.
- Replacing the CLI wizard (`jarvis/setup/wizard.py`) — it remains for headless/terminal
  installs; the GUI guide is the desktop-app counterpart.

---

## 2. Architecture

**Approach (chosen): extend the existing overlay gate into a multi-step flow.**

The current `WakeWordOnboardingGate` (`jarvis/ui/web/frontend/src/components/onboarding/`)
is a full-screen overlay mounted unconditionally in `App.tsx`. It already self-hides when
its precondition is met and fails open on error. We keep that pattern and replace its
single-step body with a stepper.

Rejected alternatives:

- **Dedicated `/onboarding` route + router redirect** — more boot-time routing logic and
  more failure surface for marginal benefit; the overlay already composes cleanly over the
  app shell.
- **Separate window / reuse the CLI wizard** — no branding, no intro clip, no in-app voice
  test; the CLI wizard stays for headless installs only.

### Component tree (frontend)

```
App.tsx
└─ OnboardingGate                 // decides visibility; fetches state; resume/skip; fail-open
   └─ OnboardingFlow              // stepper shell: progress, Gigi host, next/back/skip, persistence
      ├─ steps/WelcomeStep        // hero + <IntroClip>
      ├─ steps/TermsStep          // legal acceptance gate (cannot skip)
      ├─ steps/LanguageStep       // ui-language + reply-language
      ├─ steps/WakeWordStep       // "Hey ___" composer + notice/refs + responsibility ack
      ├─ steps/ApiKeysStep        // provider cards, "what works now" status (skippable)
      ├─ steps/MicTestStep        // mic detect + wake-word hit + TTS sample (skippable)
      ├─ steps/PersonaThemeStep   // name + tone + orb/theme (skippable)
      └─ steps/FinishStep         // summary + skipped links + example commands
   └─ components/IntroClip        // <video> if asset present, else animated Gigi fallback
```

Supporting: `hooks/useOnboarding.ts` (state, current step, skip tracking, completion);
reuse `hooks/useWakeWord.ts`, the language hooks, and the secrets/provider endpoints.

### Data flow

`OnboardingGate` mounts → `GET /api/onboarding/state`. If `onboarding_completed_at` is set
→ render nothing. Otherwise render `OnboardingFlow` starting at `onboarding_step` (resume).
Each step writes through the existing settings endpoints; the flow posts the step pointer
and skip set to the backend so closing and reopening resumes in place. `TermsStep`
acceptance posts to `POST /api/onboarding/accept-terms`. The final step posts
`POST /api/onboarding/complete`, after which the gate unmounts.

---

## 3. The Flow

| # | Step | Mandatory | Reuses |
|---|------|-----------|--------|
| 0 | Welcome + intro clip | — (can "skip setup" → minimal) | `IntroClip`, Gigi |
| 1 | **Terms & Disclaimer** | **Gate (cannot skip)** | new |
| 2 | Language (UI + reply) | — | `/api/settings/ui-language`, `/api/settings/reply-language` |
| 3 | **Wake-word** | **Required** | `/api/settings/wake-word`, `useWakeWord`, legal references + acknowledgment |
| 4 | API keys | Skippable | `/api/providers`, `POST/DELETE /api/secrets/{key}` |
| 5 | Mic / voice test | Skippable | mic probe, TTS sample |
| 6 | Persona / name + theme | Skippable | `AssistantNamePanel`, theme/orb settings |
| 7 | Finish | — | summary + links to Settings |

**Ordering note:** Welcome (0) precedes the Terms gate (1) so the very first impression is
branded; acceptance still precedes all *configuration* (steps 2+), which is the legally
stronger placement.

### Per-step detail

**0 — Welcome.** Full-screen hero: Gigi waving, app name, one-line value proposition, the
`IntroClip` slot, a primary "Get started" CTA, and an unobtrusive "Skip setup" that jumps
to a minimal path (still through the Terms gate and wake-word — both are mandatory).

**1 — Terms & Disclaimer (gate).** Renders the canonical Terms (§6) in a scrollable,
readable panel with an explicit "I have read and accept" checkbox/button. No "Next" until
accepted. On accept → `POST /api/onboarding/accept-terms` records `terms_accepted_at` +
`terms_version`. If `terms_version` later changes, the gate re-appears once.

**2 — Language.** UI language + reply language. Browser/OS language pre-selected as the
default. Setting it early means the rest of the guide renders in the chosen language. (de /
en / es today.)

**3 — Wake-word (required).** A prominent **"Hey ▢" composer**: "Hey" is a fixed prefix,
the user types the activation word. An "Advanced" disclosure exposes a fully custom phrase,
engine, and sensitivity (defaulting to `engine: "auto"`); the common path hides that
complexity. A small but clear trademark notice with informational reference links sits
under the field (§6.2 / §6.3). There is **no denylist** — any word is allowed. Before the
wake-word can be saved, the user must tick a required acknowledgment that they reviewed the
references and are solely responsible for their choice (recorded as
`wake_word_acknowledged_at`). On a valid save, the assistant speaks the chosen word back
("Hey ▢? Got it.") through TTS in the resolved output language — the first "it works"
moment. This step has no "Skip" — it gates completion.

**4 — API keys (skippable).** One card per provider class (Brain / STT / TTS / Vision /
Wake). Each card states what it powers, whether a key-free/cloud-default path already
exists, and a masked key field (first/last-3 preview, matching the existing pattern). A
prominent "Skip — add later in Settings". A **"What works right now" status** (§5) shows
green for capabilities already usable (e.g. chat) even at zero keys, turning more green as
keys are added. Keys are never accepted via voice (AP-2).

**5 — Mic / voice test (skippable).** Detect microphone → level meter → "say your wake
word" → confirm a hit → play a short TTS sample so the user hears the voice. On
headless/VPS/browser without a usable mic: a clear message ("No microphone detected — you
can talk to Jarvis by text or via a channel like Telegram") and skip. Never blocks.

**6 — Persona / name + theme (skippable).** Assistant name override (reusing
`AssistantNamePanel`), a few optional tone presets, and orb/theme appearance. Pure
personalization.

**7 — Finish.** Summary of what is configured, a short list of skipped items each linking
to the relevant Settings panel, three example commands to try, and a "Start talking to
[name]" CTA. Posts `POST /api/onboarding/complete` → sets `onboarding_completed_at`.

---

## 4. Intro Clip

`IntroClip` is a media slot, never an empty box:

- If a packaged/served video asset exists, it renders a `<video>` (muted-autoplay-capable,
  with captions and a poster), respecting `prefers-reduced-motion`.
- Otherwise it renders an **in-DOM animated Gigi sequence** (CSS/transform-based, reusing
  the existing mascot) so the flow is complete from day one with zero asset weight.

The final clip is produced later as a **Remotion composition** (React-based programmatic
video) rendered to WebM/MP4, served as a static asset by FastAPI and lazy-loaded so the
base bundle stays light for a €5 VPS. Dropping the asset in requires no flow change. Remotion
tooling lives in dev/build only — it is never a runtime dependency of the shipped app.

---

## 5. Branding & "What works now" status

- **Gigi is the through-line host.** The mascot reacts per step (waves on welcome, "listens"
  during the mic test, celebrates on finish), reusing its existing voice-state-reactive
  animations. Brand palette: yellow (`#FFE500`/`#FFB800`) on dark.
- **"What works right now" honesty banner.** A subtle, growing status that mirrors the
  project's availability-honesty doctrine (`ok` / `empty` / `unavailable`): it shows what
  the user can already do (e.g. "You can chat now") before any keys, and lights up more
  capabilities as keys are added. This removes the fear of skipping the API-key step.

---

## 6. Legal Layer

Trademark responsibility is moved transparently onto the user **by design, with no
technical blocking**. Two layers do this: an **informed notice with reference links**
(awareness) and **explicit acceptance** — both the global Terms gate and a specific
wake-word acknowledgment (responsibility). There is deliberately **no denylist**: the user
chooses any word freely and self-certifies that they reviewed the information and are
responsible. The references are informational and may be incomplete or out of date — which
is stated plainly, and is exactly why the user's own acknowledgment is what carries the
responsibility.

> Note: this design captures plain-language disclaimer text as a starting point, not
> lawyer-reviewed terms. Enforceability of liability limitations varies by jurisdiction.

### 6.1 Canonical Terms text (English, authoritative)

Stored versioned at `docs/legal/TERMS.md`; translations live as i18n strings. The English
version prevails.

> **Personal Jarvis — Terms of Use & Disclaimer (v1.0)**
>
> Personal Jarvis is free, open-source software provided "as is", without warranty of any
> kind. By using it you agree:
>
> 1. **Your responsibility.** You alone are responsible for how you configure and use this
>    software and for complying with the laws that apply to you.
> 2. **Activation word / trademarks.** You choose your own activation word and are solely
>    responsible for ensuring it does not infringe any trademark or other rights. We provide
>    informational references to help you check; they may be incomplete or out of date and
>    are not a substitute for your own check.
> 3. **No affiliation.** This project is not affiliated with, endorsed by, or sponsored by
>    Marvel, Disney, Amazon, Apple, Google, Microsoft, Samsung, or any other rights holder
>    whose products or characters may share a name with a word you could enter.
> 4. **Privacy & local-first.** This software runs locally on your own device. The
>    project's authors operate no server and do not collect, receive, or process your data.
>    Data leaves your device only through third-party services that *you* choose to connect
>    (see 5).
> 5. **Third-party services.** When you connect external services (e.g. AI provider API
>    keys), your use of those services is governed by their own terms. Your accounts, keys,
>    and any costs are your responsibility.
> 6. **Voice & recording.** This software can capture microphone audio. You are responsible
>    for using recording features lawfully, including obtaining any consent required where
>    you live.
> 7. **No liability.** To the maximum extent permitted by law, the authors and contributors
>    are not liable for any claim, damage, or loss arising from your use of the software.
> 8. **Language.** The English version of these terms is authoritative; any translation is
>    provided for convenience only.
>
> Accepting records the version and date locally so you don't see this again.

### 6.2 Wake-word trademark notice (small, clear)

English source string, shown under the wake-word field together with the reference links,
e.g.:

> "Choose your own activation word. Some names are protected by trademark (e.g. well-known
> assistants). Please review the references below — they may not be complete or current —
> and confirm you take responsibility for your choice."

### 6.3 Wake-word: free choice + informed self-certification (no technical blocking)

There is **no denylist**. The user may choose any activation word. Instead, the wake-word
step:

1. Shows a short notice that the user is solely responsible for not infringing anyone's
   trademark (§6.2).
2. Links a small, curated set of **informational references** where the user can check
   protected names — e.g. official trademark registers. Seed set (real, stable; final
   curation is a maintainer content decision during implementation):
   - EUIPO trademark search (EU) — `https://euipo.europa.eu/eSearch/`
   - USPTO trademark search (US) — `https://www.uspto.gov/trademarks/search`
   - WIPO Global Brand Database — `https://branddb.wipo.int/`
   - DPMA register (Germany) — `https://register.dpma.de/`

   The links live in one small constant (e.g. `WAKE_WORD_LEGAL_REFERENCES`) so the set can
   be curated without code changes elsewhere.
3. States plainly that **these references may be incomplete or out of date** and are not a
   substitute for the user's own check.
4. Requires an explicit acknowledgment checkbox before the wake-word can be saved:
   *"I have reviewed the information on protected names and accept that I am solely
   responsible for the activation word I choose."*

The acknowledgment is recorded (`wake_word_acknowledged_at`). The Settings wake-word API
performs **no** trademark rejection — unrestricted choice is intentional; responsibility
sits with the user.

Validation enforces only the practical limits: non-empty word, length ≤ 64 (existing cap),
and a minimum sensible length (≥ 2 chars) to avoid accidental empty/one-char phrases. No
trademark validation.

---

## 7. Data Model

Extend `data/setup_state.json` (via `jarvis/setup/state.py`), mirroring the existing
`obsidian_setup_seen_at` pattern, with:

- `onboarding_completed_at` — ISO-8601 UTC, set on completion; `null`/absent = not onboarded.
- `onboarding_step` — last active step key (for resume).
- `skipped_steps` — list of step keys the user skipped (for the Finish summary + Settings nudges).
- `terms_accepted_at` — ISO-8601 UTC.
- `terms_version` — string (e.g. `"1.0"`); re-accept required if the shipped version differs.
- `wake_word_acknowledged_at` — ISO-8601 UTC; set when the user ticks the wake-word
  responsibility acknowledgment. Required before a wake-word is saved during onboarding.

**Migration:** an install that already has `data/.setup-complete` (existing users, including
the maintainer) is treated as onboarded — set `onboarding_completed_at` on first read if
absent so the new guide is never shown retroactively. Existing wake-word config is left
untouched.

---

## 8. Backend

- **`jarvis/setup/state.py`** — add the fields above with getter/setter helpers
  (`get_onboarding_state()`, `set_onboarding_step()`, `mark_onboarding_complete()`,
  `accept_terms(version)`), atomic write, same JSON store.
- **`jarvis/ui/web/onboarding_routes.py`** (new; or extend `setup_routes.py`):
  - `GET /api/onboarding/state` → `{completed, current_step, skipped_steps, terms: {accepted, version, current_version}}`.
  - `POST /api/onboarding/step` → persist current step + skip set.
  - `POST /api/onboarding/accept-terms` → record acceptance of the shipped version.
  - `POST /api/onboarding/complete` → set `onboarding_completed_at`.
- **Wake-word legal references** — one small constant (e.g. `WAKE_WORD_LEGAL_REFERENCES`)
  exposing the curated reference links to the frontend; no denylist module, and
  `PUT /api/settings/wake-word` performs **no** trademark rejection. The acknowledgment
  (`wake_word_acknowledged_at`) is recorded via the onboarding state when the user proceeds
  from the wake-word step.
- **`jarvis/__main__.py`**:
  - `--reset-onboarding` — back up and clear the onboarding markers
    (`onboarding_*`, `terms_*` in `setup_state.json`; optionally `.setup-complete`) for a
    clean fresh-run, without touching unrelated config.
  - Honor `JARVIS_FORCE_ONBOARDING=1` → backend reports `completed: false` regardless of
    stored state (live walkthrough without deleting anything).
- **Terms doc** served/read from `docs/legal/TERMS.md`; the *current shipped* `terms_version`
  is a backend constant compared against the stored acceptance.

All config/secret writes continue through the existing atomic writer + Credential Manager
paths (AP-7 / AP-12). No new hard dependency; nothing Windows-only in the importable path.

---

## 9. Frontend Details

- **i18n:** new keys under `onboarding.*` with English source in `en.json` and translations
  in `de.json` / `es.json` (CI language-policy gate compliant). The Terms body is an i18n
  string set, English authoritative.
- **Reuse:** `useWakeWord`, language hooks, `/api/providers` + `/api/secrets/{key}`,
  `AssistantNamePanel`, theme settings.
- **Accessibility:** full keyboard navigation, focus trap in the overlay, visible focus
  rings, captions on the clip, `prefers-reduced-motion` respected for Gigi + video, the
  dialog already carries `role="dialog" aria-modal="true"`.
- **Fail-open:** if `GET /api/onboarding/state` errors, render nothing (do not trap the user
  behind a broken guide) and log — same posture as today's gate. No technical wake-word
  backstop exists by design; the legal posture rests on the accepted Terms + the wake-word
  acknowledgment, not on server-side blocking.

---

## 10. Error Handling

| Condition | Behavior |
|---|---|
| Onboarding state fetch fails | Fail open: render nothing, log. |
| API-key save fails | Inline error on the card; allow skip / retry. |
| No usable microphone (mic step) | Clear "no mic" message; offer text/channel mode; skip. |
| Intro video asset missing | Animated Gigi fallback. |
| Wake-word responsibility not acknowledged | Block proceed until the checkbox is ticked. |
| TTS sample fails | Non-fatal; show text confirmation instead. |
| App closed mid-flow | Resume from `onboarding_step` on next launch. |
| Terms version bumped | Re-show Terms gate once before continuing. |

---

## 11. Testing Strategy

### Developer fresh-run replay (the maintainer's pain point — never destroys a configured env)

1. **`?onboarding=force` (or a dev-only button)** — forces the flow to render in
   `npm run dev` regardless of stored state, touching no config. Fastest UI loop.
2. **vitest + MSW mock walkthrough** — render `OnboardingFlow` with mocked endpoints; click
   through every step without a backend. Best for design iteration.
3. **`python -m jarvis --reset-onboarding`** — backs up + clears only the onboarding markers
   for a true fresh run. Repeatable, no manual file hunting.
4. **`JARVIS_FORCE_ONBOARDING=1`** — full live walkthrough (real saves) without deleting
   real state.

### Automated tests (TDD)

- **Frontend (vitest):** gate visibility (completed vs not vs error), step navigation +
  back, skip tracking, resume from `onboarding_step`, Terms gate blocks "Next" until
  accepted, the wake-word acknowledgment checkbox gates saving (cannot proceed unticked) +
  the "Hey ___" composition, reference links render, "what works now" status.
- **Backend (pytest):** onboarding-state persistence, the endpoints, terms version
  re-accept logic, `wake_word_acknowledged_at` recording, `--reset-onboarding` clears only
  the right keys, the existing-install migration, and that `PUT /api/settings/wake-word`
  performs no trademark rejection (any non-empty word ≤ 64 chars is accepted).

---

## 12. Acceptance Criteria

1. First launch on a clean clone shows the guided flow; an already-onboarded install never
   sees it (migration verified).
2. The full flow completes with zero API keys and no microphone (graceful degradation at
   every step; no dead end).
3. Terms acceptance gates all configuration and is recorded with version + timestamp;
   bumping the version re-prompts once.
4. Wake-word selection forces a deliberate, non-empty choice; the trademark notice +
   reference links are shown; the user cannot proceed without ticking the responsibility
   acknowledgment (recorded); no word is blocked technically (the Settings API does not
   reject on trademark grounds).
5. The flow is resumable and idempotent; skipped steps are tracked and surfaced with
   Settings links on the Finish screen.
6. `IntroClip` shows the animated Gigi fallback when no video asset is present.
7. All four developer replay paths work; the automated test buckets pass; `ruff` clean; the
   CI language-policy gate passes (English source strings).

---

## 13. Open Items / Future

- Produce the final Remotion intro clip and drop it into the `IntroClip` slot.
- Curate / localize the `WAKE_WORD_LEGAL_REFERENCES` link set and keep it reviewed over time.
- Optional: a guided "connect a channel" (Telegram/Discord) sub-step for headless users.
