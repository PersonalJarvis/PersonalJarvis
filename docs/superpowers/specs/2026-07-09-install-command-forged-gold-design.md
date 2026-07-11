# Install Command "Forged Gold" Redesign — Design

**Date:** 2026-07-09 · **Status:** approved, amended 2026-07-11

## Problem

The install experience reads as generic ("AI-sloppy") rather than branded. Diagnosis:

1. **Off-brand color.** The installer (Stage 1 shells + Stage 2 `installer.py`) uses a
   washed-out beige gold `#e7c46e`, while the brand (docs/BRAND.md) is Signal Yellow
   `#FFD60A` on matte black with the forged-gold gradient
   `#FFE552 → #FFD60A → #B8960A`.
2. **The rich-panel finale.** The boxed `✓ Done` panel is the signature look of the
   `rich` library seen in countless generated projects. Modern installers (uv, bun)
   end flat and calm.
3. **No sense of journey.** Steps appear one by one with no indication how many
   remain or where the user is.

Decisions made by the maintainer:

- **Install URL stays** (`raw.githubusercontent.com/...`) — no short domain for now.
- **Terminal look becomes variant A "Forged Gold"** (branded banner gradient,
  numbered phases, flat finale).
- The normal path stays prompt-free. Amendment 2026-07-11: if Python or Git is
  missing, Stage 1 asks once before installing those system prerequisites,
  re-checks them in the same process, and continues without a second command.
  All product setup consent remains in app onboarding, and **declining the
  Terms quits the app**.

## Design

### 1. Shared installer palette (both stages)

| Token | Hex / RGB | Use |
|---|---|---|
| gold-hi | `#FFE552` (255,229,82) | banner top rows |
| gold (brand) | `#FFD60A` (255,214,10) | banner mid rows, phase markers, accents |
| gold-deep | `#B8960A` (184,150,10) | banner bottom rows, finale rules |
| ok | `#7ac88c` | check marks |
| muted | `#8F8F8F` | secondary text (was `#8c8c8c`) |
| bad | `#e07a6e` | errors |

Stage 1 keeps the existing TTY guard (no escapes when piped/dumb) and the
PowerShell ASCII-source rule (non-ASCII only inside the banner here-string,
glyphs from code points). No cursor repositioning anywhere — output stays
strictly append-only so dumb terminals and CI logs never break.

### 2. Banner (Stage 1 only, unchanged on update runs)

Six ANSI-shadow rows colored as a vertical gradient — rows 1–2 gold-hi, rows 3–4
gold, rows 5–6 gold-deep — replacing the flat single-color banner. The
`P E R S O N A L` prefix line is dropped; below the art:

```
  P E R S O N A L  J A R V I S   ·   talk to your computer
  Checks prerequisites · installs the full profile · launches when done
```

(first line muted letter-spaced caps, second line muted). The subtitle line
("Quick install · Windows") is absorbed by the tagline block.

### 3. Numbered phases across both stages (the journey)

One fixed six-phase journey; Stage 1 owns 1–3, Stage 2 owns 4–6:

| # | Phase | Stage | Contains |
|---|---|---|---|
| 1/6 | Prerequisites | 1 | Python+Git detect/install/re-check; optional Node check |
| 2/6 | Fetching Personal Jarvis | 1 | clone/update, payload pin |
| 3/6 | Python environment | 1 | venv + bootstrap deps (merged) |
| 4/6 | Dependencies | 2 | pip editable + lockfile + extras |
| 5/6 | Voice models | 2 | prefetch + on-disk verification |
| 6/6 | Finish & launch | 2 | worker CLI, shortcut, UI check, summary, launch |

Phase header format (both stages): gold `N/6` + bold title. Sub-results keep the
existing `✓` / note / `✗` grammar. Stage 2 prints its environment info (platform,
python, path, headless) as muted lines before phase 4/6 instead of an unnumbered
"Environment" step. Running `installer.py` directly (rare, manual) starts at 4/6
by design — the numbering documents the journey, not the entry point.

### 4. Flat finale (replaces the rich Panel)

```
  ──────────────────────────────────────────────── (gold-deep rule)
  ✓ Personal Jarvis is ready.            (ok + bold; "updated" on update runs)
    Installed to  ~/.personal-jarvis
    Start again   jarvis        (gold)
    Update        re-run the same install one-liner — settings are kept
    Next          the app opens with a one-time setup guide (language,
                  wake word, API keys); it never shows again
  ──────────────────────────────────────────────── (gold-deep rule)
  Launching — the app takes over from here…       (muted; omitted on --no-launch)
```

Update runs keep the "no re-onboarding" promise line. Headless runs keep the
server-address hint.

### 5. Conditional prerequisite consent, tightly scoped

Amendment 2026-07-11 supersedes the original fully prompt-free Stage-1 rule.
Both shells first evaluate Python 3.11+ and Git. When both exist, they print the
versions and continue without a prompt. Otherwise they list the missing items
and ask once before invoking WinGet, Homebrew, or the detected Linux package
manager. The package-manager process is awaited, the current PATH/command cache
is refreshed, and both prerequisites are re-checked. A rare failed/manual path
keeps the same installer alive for re-check/retry until the user succeeds or
explicitly quits. `JARVIS_INSTALL_PREREQS=auto` is the explicit unattended
consent path; `never` forbids prerequisite installation.

The static guard now confines `Read-Host`/TTY `read` calls to the marked
prerequisite state machine. `installer.py` remains fully prompt-free, so the
terminal wizard or product-setup questions cannot leak back into installation.

### 6. Onboarding: declining the Terms quits the app

The existing RiskGate (checkbox + proceed) gains an equal-weight **Decline**
path:

- **Frontend** (`RiskGate.tsx`): a second button ("Decline & quit"); clicking it
  calls the new endpoint, then renders a terminal "Jarvis has been closed —
  reopen the app to decide again" state (relevant on the browser/headless
  surface; the desktop window disappears with the process). New i18n keys
  `onboarding.risk.decline`, `.declined_title`, `.declined_body` in de/en/es.
- **Backend** (`onboarding_routes.py`): `POST /api/onboarding/decline-terms` —
  409 if Terms are already accepted (a declined gate can only exist before
  acceptance; also keeps the endpoint from being a gratuitous kill switch),
  otherwise responds `{ok: true, quitting: true}` and schedules a shutdown.
- **Shutdown mechanics:** desktop hosts get `DesktopApp.request_quit()` — the
  `request_restart()` quit sequence (mark quit → destroy window → hard-exit
  fallback) **without** spawning the relauncher. Headless hosts fall back to a
  short-delayed hard exit so the HTTP 200 flushes first. Next start shows the
  gate again — nothing is persisted on decline.

## Out of scope

- Short install domain (explicitly deferred by the maintainer).
- README install-section rewrite (URL unchanged; copy already matches).
- Any change to the onboarding steps themselves (language/wake word/API keys).

## Testing

- `pytest tests/unit/install/` — shell state-machine tests, scoped-prompt guard,
  existing flow and Python-detection regressions.
- `pytest tests/unit/ui/` routes tests for the decline endpoint (409 after
  acceptance, quit scheduling stubbed).
- Frontend: RiskGate decline-state test via vitest.
- Manual: `installer.py --dry-run` (desktop + `--headless`) transcript review on
  Windows; POSIX path exercised via `bash -n` syntax check + CI.
