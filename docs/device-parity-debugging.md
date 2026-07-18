# Device Parity Debugging — "works on the dev box, broken on device X"

**The single most expensive misdiagnosis in this project:** a feature behaves
differently on a second device (test Windows box, test Mac, a fresh install)
and the difference is treated as a code bug. Historically, most of these
reports were NOT code bugs — they were one of three divergence layers below.
This document is the binding triage ritual: check the layers **in order**, and
never diagnose a cross-device defect without stating which layer it is.

---

## Why a second device is never "the same Jarvis"

The dev box runs the live working tree: every fix takes effect immediately,
including uncommitted ones, on top of a configuration that has grown for
months (multiple provider families with keys, a trained wake word, realtime
mode, wiki content, activated skills). A second device runs **the published
code** with **an empty or minimal setup**. Identical code therefore does NOT
mean identical behavior — by design, features gate on capabilities and
degrade quietly when a key or provider is missing (§3 of `CLAUDE.md`).

## Layer 1 — Version lag (check FIRST, takes 2 minutes)

The device may simply run older code.

- **On the device:** read the running version in the app (top bar / Settings →
  About) or `GET /api/update/status` → `current`. Do not trust "I just
  reinstalled" — reinstalls have picked up stale checkouts and pre-release
  builds before.
- **On the dev box:** `git describe --tags`, then
  `git log --oneline public/main..main` (unpublished commits) and
  `git status --short` (uncommitted work — exists on the dev box ONLY).
- **Delivery pipeline facts:** a fresh install clones public `main`
  (`install/install.sh` / `install.ps1`, `JARVIS_INSTALL_REF` defaults to
  `main`); the in-app updater (`jarvis/ui/web/update_routes.py`) moves a
  managed install only between **published GitHub Releases** — pushing a tag
  without publishing a Release updates nobody.

A fix that lives only as an uncommitted edit or an unpublished commit does
not exist anywhere else in the world. Ship it before expecting it on a device.

## Layer 2 — Setup divergence (the usual culprit after a fresh install)

Configuration, credentials, and data **never travel with the code** — that is
deliberate credential protection, not a sync failure: `data/`, `.env`,
`jarvis.toml`, the OS keyring, and the Vault are untracked (§2 of
`CLAUDE.md`).

A fresh install therefore starts with: no keys, default providers, default
mode, no wake word, no wiki, no skills, no trained voices. Because every tier
resolves through key-aware fallback chains (AP-22), a missing key does not
error — it silently lands on a simpler path or a degradation message. To the
user this reads as "the feature is broken on this device".

**Compare the two machines' setups before debugging anything:**

- **The one-call answer: `GET /api/setup-report`** on each running instance
  (`?format=text` for a human-readable version; also reachable as
  `jarvis api setup-report get-setup-report`). It is share-safe by
  construction — key **presence** booleans only, never values — and its
  `summary` lines are ordered for line-by-line diffing between two devices;
  `degradations` names every tier that quietly fell back or lacks a key.
- Manual cross-check: Settings → Providers on both devices; mode (realtime
  vs. classic pipeline), wake word set, skills activated;
  `python -m jarvis --check` on both, and diff the output.

If the setups differ, align them (in-app — §3 requires every credential path
to be recoverable in-app) and re-test **before** filing a bug.

## Layer 3 — OS gaps (only after layers 1+2 match)

Only when the device runs the same version with an equivalent setup and a
feature still misbehaves is an OS-specific defect plausible (macOS
permissions, window control, audio backends). Then the OS-parity rules apply:
`docs/os-parity.md` + §3 "OS feature parity" in `CLAUDE.md`. File it as a
tracked parity gap, not folklore.

---

## Release completeness (the layer-1 prophylaxis)

A release ships the ENTIRE current local state (§2). The checks below are
automated as a fail-closed gate — run it before tagging, from the repo root:

```bash
python scripts/ci/check_release_completeness.py                  # pre-tag
python scripts/ci/check_release_completeness.py --verify-release # post-publish
```

What it enforces (and why each item exists):

1. **Dirty tree** — every dirty file is either committed by its owning
   session or **explicitly excluded via `--ack-dirty`** (the gate prints the
   list). Never silently cut a release that lacks visible local fixes.
2. **Reconcile** — the branch must not be behind `public/main`.
3. **Published Release** (`--verify-release`) — the GitHub **Release** must be
   published, not just the tag pushed; the in-app updater follows
   `releases/latest`, so a tag alone updates nobody.
4. **Dist freshness** — the frontend `dist/` bundle must be at least as new
   as the frontend sources (a stale bundle ships invisible-old UI).
5. **Version parity** — `jarvis/__init__.py` and `pyproject.toml` agree.
