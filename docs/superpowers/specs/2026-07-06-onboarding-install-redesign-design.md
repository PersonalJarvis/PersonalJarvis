# Install & First-Run Onboarding Redesign — Design Spec

**Date:** 2026-07-06
**Status:** Approved by maintainer (design review in session)
**Scope:** `install/installer.py`, `install/install.ps1`, `install/install.sh`,
`jarvis/__main__.py`, `jarvis/setup/wizard.py` (invocation only),
`jarvis/ui/web/fast_bootstrap.py`, `jarvis/ui/web/onboarding_routes.py`,
onboarding frontend (`jarvis/ui/web/frontend/src/components/onboarding/`),
`jarvis/ui/icon_utils.py` (invocation point), `.github/workflows/fresh-install-smoke.yml`.

---

## 1. Problem statement

A fresh-machine test of the public install one-liner surfaced three defects:

1. **The desktop onboarding never shows.** The installer runs the interactive
   8-step terminal wizard (`installer.py:243-255` → `python -m jarvis --wizard`),
   and the wizard's finalize step writes BOTH completion markers
   (`wizard.py:853-854`: `mark_onboarding_complete()` + `cfg.mark_setup_complete()`).
   The desktop gate computes `completed = onboarding_completed_at is not None OR
   not is_first_run()` (`onboarding_routes.py:61-70`), so the polished in-app
   onboarding (`OnboardingGate.tsx`, 5 steps + risk gate + intro video) is
   permanently suppressed. The terminal wizard duplicates its content in a worse
   medium (~15+ sequential `input()` prompts).
2. **The first boot looks like a broken/old app.** On a fresh machine the first
   boots download the Whisper wake/STT models (`fwhisper.py:141-169`) inside the
   deferred warmup (`desktop_app.py:1593-1625`); until `set_app`, every
   `/api/*` route answers 503/placeholder (`fast_bootstrap.py:76-140`). The SPA
   renders with empty lists, a stuck "starting up" banner (`server.py:442-478`),
   a generic Python taskbar icon (AUMID shortcut only takes effect on the NEXT
   launch, `icon_utils.py:130-142`), and a console window. After ~5-6 restarts
   the model cache is warm and the icon resolved — "suddenly the right app".
   Worse: the onboarding state fetch itself 503s during warmup and the gate
   fails open (`OnboardingGate.tsx:44-46`), so onboarding cannot even appear on
   the boot where it matters most.
3. **Re-onboarding after updates** is a stated requirement. The current gate
   logic already never re-triggers on update (no version comparison feeds
   `completed`), but nothing pins that behavior; the historic "reappears every
   restart" bug shipped twice (CWD-relative state path; persisted pre-flow acks).

Additional gap found during analysis: the desktop flow has **no persisted Terms
acceptance**. `POST /api/onboarding/accept-terms` exists
(`onboarding_routes.py:102-105`) but no frontend caller; `RiskGate.tsx` keeps
its acknowledgement in local React state only. Today the terminal wizard is the
only place Terms are recorded — removing it silently removes the legal record.

## 2. Goals

- The install one-liner is **fully non-interactive**: it downloads and prepares
  *everything* (code, Python deps, voice models, worker CLI, shortcuts/icon),
  explains each step in one plain-English line, and launches the app **as its
  last action**. When the installer says "done", the app is genuinely ready.
- The **first app launch reliably shows the real desktop app** with the
  onboarding flow as the first thing the user sees. No broken-looking interim
  state, no restarts needed.
- Onboarding shows **exactly once**: completing it (or skipping to finish)
  persists; updates and restarts never bring it back. `--reset-onboarding`
  and `?onboarding=force` remain the only replay paths.
- Headless/VPS installs keep an equivalent path (browser onboarding), and the
  terminal wizard remains available as an explicit opt-in tool.

### Non-goals

- No redesign of the onboarding steps themselves (5-step flow, risk gate and
  intro video stay as-is).
- No return of the removed MicTest/SystemStyle/standalone-Terms steps.
- No GPU/`[local-voice]` extras in the base install (stays opt-in, AP-25).
- No changes to the in-app updater (`update_routes.py`).

## 3. Design

### A. Installer: download everything, ask nothing, explain everything

**A1. Remove the wizard from the install path.**
- Delete `step_wizard` from `installer.py`'s default sequence. No completion
  marker is written during install — that is what re-arms the desktop
  onboarding for the first launch.
- `python -m jarvis` (bare) no longer auto-enters the wizard on
  `is_first_run()` (`__main__.py:493-498`); it starts the app. The wizard stays
  reachable via explicit `python -m jarvis --wizard` (SSH-only setups,
  power users) and still writes both markers when completed there.
- The non-interactive wizard path (`wizard.py:918-933`) must NOT be invoked by
  the installer anymore either — previously it silently wrote
  `.setup-complete` on headless installs, which would suppress the browser
  onboarding for VPS users.

**A2. New model prefetch step — shared with the app.**
- New CLI entry `python -m jarvis --prefetch` that downloads every artifact the
  default voice path needs at runtime: the wake Whisper model and the utterance
  STT model (CPU/base variants only — the universal set; GPU extras stay
  opt-in). Implemented as a thin module `jarvis/setup/prefetch.py` that
  reuses the exact model-resolution code the runtime uses, so installer and app
  can never drift on *which* models to fetch.
- `installer.py` gains `step_models` calling that entry with a progress bar and
  a one-line explanation including approximate download size.
- **Failure policy:** retry once; on persistent failure print an honest warning
  ("voice models will download on first launch instead") and continue —
  a flaky mirror must not brick the install (§3 universality). The runtime
  keeps its existing lazy-download fallback as the safety net.

**A3. Worker CLI and shortcuts move into the installer.**
- The `claude` worker-CLI npm install (formerly wizard step 6,
  `wizard.py:700-707`) becomes a best-effort installer step: present when
  node/npm exist, otherwise a single honest note that the Jarvis-Agent worker
  needs Node.js and can be added later in-app. Never fatal.
- The Start-Menu shortcut / AUMID registration (`icon_utils.py`) runs during
  install (Windows only, capability-gated, best-effort) instead of during the
  first app run, so the very first taskbar button already has the correct name
  and icon.

**A4. Explanatory output contract.**
Every step prints: what is happening, why, and (for downloads) size/progress.
The final summary states exactly what happens next:
"Launching the Jarvis Desktop App now — it will walk you through setup
(language, wake word, API keys). This setup runs only once."
Then `step_launch` runs **last**, unchanged in mechanism (`Popen`, no wait).

**A5. Update runs.**
Stage 1 already distinguishes update vs fresh (`install.ps1:142-163`). The
installer surfaces it: on an update run print "Updating existing install —
your setup and settings are kept", skip nothing else (pip sync, prefetch are
idempotent), never touch `data/setup_state.json` / `.setup-complete`, and do
not re-launch into onboarding (the markers guarantee it).
- Fix the doc contradiction: `install/README.md:18-23` (one-liner "not active")
  vs root `README.md:97-116` — align both with the real, working one-liner.

### B. First launch: reliably the real app

**B1. Onboarding state joins the fast-boot path.**
`/api/onboarding/state` (and `accept-terms`, `step`, `complete`) must be served
by the early bootstrap app, not held behind `set_app` — they only read/write
`data/setup_state.json` and import nothing heavy. Mount a minimal onboarding
router in `fast_bootstrap.py` (exempting the prefix from the 503 hold is not
enough — the real routes only exist after `set_app`) so the gate can render
from the first second. Boot-budget note
(AP-26): the state file read is O(one small JSON) and stays off the heavy path.

**B2. Gate retries instead of failing open on warmup.**
`useOnboarding` polls the state endpoint (short backoff, bounded ~30 s) while
it returns 503/network-error before the gate gives up. Fail-open stays as the
terminal behavior (never trap the user), but a warming backend no longer
swallows the first-run onboarding.

**B3. Honest warmup status instead of an empty shell.**
While the backend warms behind the SPA, the UI shows one clear global boot
status ("Jarvis is starting …" with the voice-readiness state it already
receives via `VoiceBootStatus`) instead of empty feature lists and a stuck
banner. With models prefetched at install time this window shrinks to seconds;
the status is the honest fallback when prefetch failed (A2 failure policy).

### C. Desktop onboarding completeness

**C1. Persist Terms acceptance.** `RiskGate` (which already blocks on an
explicit checkbox) calls `acceptTerms()` (`useOnboarding.ts:64` →
`POST /api/onboarding/accept-terms`) on proceed, with the Terms text reachable
from that screen (`GET /api/onboarding/terms`). Write remains fire-and-forget
for UX (failure logged, gate still proceeds — fail-open doctrine) but the
happy path now records `terms_accepted_at`/`terms_version`. The pre-flow ack
itself stays local-state-only (restart-loop defense) — only the *terms record*
is persisted, never consulted by the gate's `show` logic.

**C2. Autostart toggle in FinishStep.** The "start Jarvis at login" question
(formerly wizard finalize, default YES) becomes a toggle on the finish step,
wired to the existing autostart mechanism; capability-gated per OS, hidden
where unsupported (headless Linux).

**C3. Pin the no-re-onboarding guarantee.** New regression tests:
- Backend: completed markers set + any simulated version bump ⇒
  `/api/onboarding/state` reports `completed: true` (no version field may ever
  feed `completed`).
- Frontend: gate stays hidden when `completed: true` regardless of
  `terms.accepted_version != terms.current_version`.

### D. Headless / cross-platform parity

- Headless install: same silent installer; final summary prints the real bound
  URL (existing `_resolved_admin_port()` logic) with "open this address in your
  browser — the same one-time setup runs there". The browser SPA serves the
  identical onboarding (it already is the web UI), now reachable immediately
  thanks to B1.
- Prefetch downloads only universal CPU models; runs on `python:3.11-slim`
  without keyring/GPU/audio (no audio devices are touched at install time).
- The three §3 non-maintainer paths (fresh install with one arbitrary key,
  headless Linux, cross-family fallback) are part of the definition of done.

### E. CI: make the smoke test see what users see

Extend `fresh-install-smoke.yml` beyond "any `GET /` returns 200":
1. After install, `GET /api/onboarding/state` answers immediately (< a few
   seconds after process start) with `completed: false`.
2. `POST /api/onboarding/complete`, restart the process, state now reports
   `completed: true` — the no-re-onboarding contract, exercised end-to-end.
3. Served `index.html` references the freshly built asset hash (stale-`dist/`
   guard against the "old app" failure mode; build parity with the shipped
   bundle).
4. Assert the installer output contains no interactive prompt markers (guards
   against a wizard invocation regressing into the install path).

## 4. Error handling summary

| Failure | Behavior |
|---|---|
| Model download fails during install | Warn honestly, continue; runtime lazy-download remains the fallback (B3 shows honest status). |
| npm/node missing | One-line note; worker CLI installable later in-app. Never fatal. |
| Shortcut/AUMID registration fails | Best-effort, logged, never fatal (non-Windows: silent no-op). |
| Onboarding state fetch 503s at first paint | Frontend polls with backoff (B2); terminal fail-open preserved. |
| `accept-terms` POST fails | Logged, flow proceeds (fail-open); record written on next successful call. |
| Update run | Markers untouched; onboarding cannot reappear. |

## 5. Testing

- Unit: prefetch module (model list = runtime list, retry/failure paths);
  installer step ordering (launch is last, no wizard step, no marker writes);
  `__main__` no longer auto-wizards; onboarding fast-path routes; C3 tests.
- Frontend: RiskGate calls `acceptTerms`; gate retry/backoff behavior;
  FinishStep autostart toggle; existing parity tests stay green.
- Integration/CI: extended fresh-install smoke (E1-E4).
- Manual (fresh VM): one-liner → zero prompts → app launches last → onboarding
  appears immediately → complete → restart → no onboarding → re-run one-liner
  (update) → no onboarding.

## 6. Decided trade-offs

- **Prefetch via shared `--prefetch` CLI** (not installer-local download code):
  one source of truth for the model set; the app reuses it as its fallback.
- **Install gets slower** (model download moves into it) in exchange for a
  first launch that is genuinely ready — explicit maintainer decision.
- **Terminal wizard kept as opt-in only**: preserves an SSH-only setup path at
  near-zero maintenance cost; removing it entirely would strand headless users
  who cannot open a browser to the host.
- Mic-test / hotkey prompts are not resurrected in the desktop flow (removed
  deliberately on 2026-06-20; wake-word step handles mic degradation honestly).
