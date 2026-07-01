# ADR-0015 — Obsidian Setup Wizard

**Status:** Accepted · **Date:** 2026-05-14 · **Phase:** B9 (Obsidian Onboarding)

## Context

Phases B0–B7 built the entire Wiki-Memory subsystem: a Markdown vault
under `wiki/obsidian-vault/`, an atomic write pipeline (`AtomicWriter`),
the WikiCurator pipeline (B1), the SessionRollupWorker (B7), the
WikiContextInjector and the VoiceFactBridge (B5/B8), and a desktop
Wiki tab with live-reload and graph view (B3). All of that produces a
fully-shaped vault on disk — but Jarvis never registers that vault with
the user's Obsidian app. The user can install Obsidian, launch it, and
get a "no vaults yet" prompt while a perfectly-populated vault sits in
`%USERPROFILE%\Desktop\Personal Jarvis\wiki\obsidian-vault\` waiting
to be opened.

The pre-B9 user-story therefore had two invisible failure modes. First,
"Obsidian is not installed" — Jarvis had no way to tell, so the Wiki
tab's "Open in Obsidian" button silently did nothing (the
`obsidian://` URL scheme is a no-op when the handler is unregistered).
Second, "Obsidian is installed but the vault is not registered" — the
user would launch Obsidian, see no vaults, give up, and never discover
that the vault file existed at all. The whole Wiki-Memory user-story
is invisible without this onboarding step.

Two additional pressures shaped the design. The Wiki-tab is the only
place where this onboarding matters — opening Settings or running a
CLI wizard far away from the Wiki context defeats the discovery goal.
And `obsidian.json` (the file that registers vaults with the Obsidian
app) is owned by an external program; Jarvis must write it carefully,
because a corrupt `obsidian.json` would brick the user's Obsidian
install, not just Jarvis.

A live-probe during the B9 build also surfaced a deployment fact that
contradicts every Obsidian help thread on the internet: on this user's
machine the Obsidian executable lives under `%PROGRAMFILES%\Obsidian\`,
not the documented per-user `%LOCALAPPDATA%\Obsidian\` default. A
detector that only knows about the default would have shown "not
installed" forever — a silent false negative that would have masked
the whole onboarding flow.

## Decision

The B9 onboarding wizard is a small, narrowly-scoped feature governed
by six contracts.

- **Pure detector + write split.** `detect_obsidian()`,
  `read_obsidian_vaults()`, and `is_vault_registered()` are read-only
  and never mutate disk. `register_vault()` is the sole mutator. The
  REST routes mirror this split — `GET /api/setup/obsidian/status` is
  pure, `POST /api/setup/obsidian/register` writes. This makes the
  feature trivially auditable: any disk write goes through one
  function in one file.

- **Atomic write contract.** `register_vault()` follows the same
  five-step pipeline that ADR-0009 (self-mod) established for
  `jarvis.toml` writes: read existing `obsidian.json` → apply mutation
  in memory → write to tempfile in the same directory → `os.replace()`
  atomically over the original → post-write verify by re-reading and
  asserting the vault entry is present, restoring the backup on any
  failure. The backup file is named
  `obsidian.json.b9-backup-YYYYMMDD-HHMMSS` to make manual recovery
  obvious. The pattern is deliberately inlined rather than abstracted
  into a shared helper, because this is a once-per-onboarding write
  and an inline form is easier to audit than a parametrised generic
  writer that has to grow assertions for every new caller.

- **REST routes never 5xx on detection failures.** Any exception
  raised inside `detect_obsidian()` — pywin32 missing, registry
  permission denied, `obsidian.json` corrupt — is caught by the
  route handler and returned as `200 OK` with
  `recommended_action="ok"` and a `note` field describing the
  failure. The UI must remain responsive even on a half-broken
  Windows install; the Wiki tab cannot become a hard gate that
  blocks the user from reading their own notes because a registry
  call returned `ERROR_ACCESS_DENIED`. The status pill in that case
  shows "Status unklar" (a new fourth pill colour) rather than
  hiding the entire tab.

- **First-run heuristic with conservative dismissal.** A flag in
  `data/setup_state.json` (`obsidian_seen: true|false`) controls
  whether the setup dialog auto-opens on Wiki-tab first paint.
  Pressing Esc, clicking outside the dialog, or clicking the close
  X does **not** set the flag — only the explicit
  "It worked — Done" button does. The asymmetry is deliberate:
  an accidental dismissal must not lock the user out of the help
  flow they have not yet completed; only an affirmative "I'm done"
  click marks the onboarding as resolved. The flag can be reset by
  deleting the file, and the dialog remains reachable from a manual
  "Status" pill click forever.

- **3-step German-language walkthrough.** The dialog steps the user
  through Install → Connect → Live-Test in three numbered cards.
  The Live-Test step launches Obsidian directly via the
  `obsidian://open?vault=<urlencoded-name>` URL scheme — the only
  zero-install way for a browser to invoke a native application
  handler. If the handler is unregistered (Obsidian not installed)
  the browser silently does nothing, which is the cheapest possible
  failure mode for a happy-path button. The walkthrough is German
  because the user-facing Jarvis UI is German; the API surface and
  the code remain English per the Output Language Policy.

- **System-wide install support in the detector.** `detect_obsidian()`
  probes three locations in order: (a) the registry handler for
  `obsidian://` URL scheme under `HKCU\Software\Classes\obsidian\`,
  (b) the documented per-user default
  `%LOCALAPPDATA%\Obsidian\Obsidian.exe`, and (c) the system-wide
  install path `%PROGRAMFILES%\Obsidian\Obsidian.exe`. The third
  probe was added because the live-probe during the B9 build found
  the executable there on the developer machine; without it the
  feature would have reported "not installed" on the very machine
  it was being built on.

## Consequences

**New artefacts the user accumulates**

- `data/setup_state.json` is created on first Wiki-tab paint after
  install. Any backup or sync flow the user runs (rsync, git-ignore,
  the End-of-Day-Auto-Push script under `scripts/auto-push-eod.ps1`)
  should treat it as user state — losing it just re-triggers the
  first-run dialog once, no data loss.
- `obsidian.json.b9-backup-YYYYMMDD-HHMMSS` files accumulate under
  `%APPDATA%\obsidian\` on every register operation. We deliberately
  do **not** garbage-collect them — that directory is owned by the
  Obsidian app, not by Jarvis, and the safe default is "leave the
  user's home for the user to manage". The backups are small (a
  few hundred bytes of JSON).

**UI surface area**

- The Wiki sidebar gains a new "Status unklar" pill state — a fourth
  colour beyond the existing green/yellow/red. This is a visible
  system-health indicator for the Obsidian connection that did not
  exist before; first-time users will see it transition through
  yellow ("nicht registriert" — not registered) to green ("verbunden" — connected) as they  <!-- i18n-allow -->
  complete the walkthrough.
- The Wiki-tab now does one read-only HTTP probe (`GET
  /api/setup/obsidian/status` and `GET /api/setup/state`) on first
  paint per session, used to decide whether to auto-open the dialog.
  The probe is cached for the session; subsequent re-mounts of the
  Wiki view do not re-probe.

**External-app touch surface**

- This is the first feature in the codebase that writes to a file
  owned by another desktop application. The atomic-write contract
  exists specifically to prevent the worst-case outcome — a partial
  write that corrupts `obsidian.json` and renders Obsidian unable
  to start. Any future feature that touches an external app's
  config (Slack, Notion, etc.) should adopt the same five-step
  pipeline and the same `<file>.b9-backup-*` naming convention.

## Alternatives Considered

**System-tray notification on first detect.** Rejected. The user is
already in the Wiki tab when the onboarding gap matters — the system
tray is invisible at that moment and would require a context switch
("where did that notification go?") to find. The dialog appears in
the same surface the user is already looking at.

**CLI wizard (`python -m jarvis --wizard --obsidian`).** Rejected.
B9 is a UX-first feature for a non-coder user. Hiding the
onboarding behind a CLI command defeats the purpose: the user would
need to know the CLI flag exists, open a terminal, and run a
Python module — a workflow this user does not perform. The CLI
wizard remains available for the initial setup (API keys, profile),
but the Obsidian flow lives where the user discovers the problem.

**Always-on auto-register (no dialog).** Rejected. Writing to
`obsidian.json` without explicit user consent is hostile, especially
because it modifies a file owned by an external app the user has
their own relationship with. The user might already have a vault
they prefer; silently injecting Jarvis's vault as a new entry would
be a violation of trust. The dialog makes the write opt-in and lets
the user see the destination path before committing.

**Use `obsidian://` deep-link to do registration instead of writing
`obsidian.json` directly.** Rejected. There is no Obsidian URL
scheme verb for "register this vault path"; the URL scheme only
supports `open?vault=<name>` against already-registered vaults.
Writing `obsidian.json` is the only mechanism available today.

**Skip detection of system-wide installs.** Rejected during the
live-probe. The developer machine has Obsidian under Program Files,
and "we don't support that location" would have been a false
negative on the very machine the feature was built on. The cost of
the extra probe is one filesystem stat on first paint.

## References

- [`0013-knowledge-wiki-architecture.md`](0013-knowledge-wiki-architecture.md)
  — the long-term memory tier whose user-story B9 makes visible.
  Establishes the three-tier memory hierarchy and the
  `AtomicWriter` pattern that this ADR's `register_vault()` mirrors.
- [`0014-memory-trigger-contract.md`](0014-memory-trigger-contract.md)
  — sibling B8 ADR. Same overall shape: a phase that lands a
  contract for an external-facing surface (silent vs loud failure
  there, conservative-dismissal vs aggressive-write here) so the
  feature's failure modes are explicit.
- [`0009-self-healing-worker-critic.md`](0009-self-healing-worker-critic.md)
  — origin of the five-step atomic-write pipeline (Pre-Validate →
  Backup → Tempfile → `os.replace` → post-write verify with
  rollback). B9 applies the same shape to `obsidian.json`.
- `docs/obsidian-setup.md` — the user-facing companion document
  shipped alongside this ADR. ADR-0015 covers the engineering
  contract; `obsidian-setup.md` covers the user walkthrough.
- `jarvis/setup/obsidian.py` — implementation. `detect_obsidian`,
  `read_obsidian_vaults`, `is_vault_registered`, `register_vault`.
- `jarvis/setup/state.py` — first-run flag store.
- `jarvis/ui/web/setup_routes.py` — REST surface. Four routes,
  all 200-OK-on-error per the contract above.
- `jarvis/ui/web/frontend/src/components/wiki/ObsidianStatus.tsx`,
  `ObsidianSetupDialog.tsx`, `views/WikiView.tsx` — frontend
  surface. The first-run probe lives in `WikiView.tsx`.
