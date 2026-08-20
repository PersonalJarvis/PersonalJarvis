# End-of-Day Auto-Push for Personal Jarvis

## What does it do?

The **primary** path is live: after each completed commit on the shared primary checkout, agents `git push` (see `docs/agent-contract.md` §2 / §9). This script is the **crash-backup**. It mirrors remaining local branches to GitHub every evening and sets a **backup tag** (`safety/eod-<branchname>-<timestamp>`) first, so a session that died between commit and push still lands, and even destructive follow-up actions stay reversible.

Background: On 2026-05-01 a restore went well only because you instinctively set backup tags. This automation still does both (tag + push) every evening as the backstop, without you having to think about it.

---

## Activate (1 command)

In PowerShell (no admin required):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Administrator\Desktop\Personal Jarvis\scripts\install-auto-push-task.ps1"
```

Default: daily at 22:00. Different time:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Administrator\Desktop\Personal Jarvis\scripts\install-auto-push-task.ps1" -Time "23:30"
```

---

## Deactivate (1 command)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Administrator\Desktop\Personal Jarvis\scripts\uninstall-auto-push-task.ps1"
```

---

## Trigger manually right now

```powershell
Start-ScheduledTask -TaskName "Personal-Jarvis-EoD-Push"
```

Or run the script directly (even without an installed task):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Administrator\Desktop\Personal Jarvis\scripts\auto-push-eod.ps1"
```

**Dry run** (only shows what would be done, pushes nothing):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Administrator\Desktop\Personal Jarvis\scripts\auto-push-eod.ps1" -DryRun
```

### Hotkey idea (optional, nice-to-have)

Place a small `.lnk` shortcut to the script on the desktop, then assign a hotkey via the Windows shortcut properties under "Shortcut key", e.g. `Ctrl+Alt+P`.

---

## Where is the log?

```
C:\Users\Administrator\Desktop\Personal Jarvis\logs\auto-push-eod.log
```

Format: `[YYYY-MM-DD HH:MM:SS] [LEVEL] message`. Levels:

- **OK**     — push successful
- **INFO**   — normal workflow step
- **WARN**   — non-critical (e.g. tag already exists)
- **SKIP**   — something was deliberately skipped
- **FAILED** — a specific branch could not be pushed
- **FATAL**  — script aborts (no repo, no fetch possible)

---

## What to do on "FAILED" in the log?

Three common cases:

### 1. `(auth)` — authentication failed

GitHub doesn't recognize you. Solution: GitHub CLI re-auth:

```powershell
gh auth login
```

Or set a new Personal Access Token in `git credential manager`.

### 2. `(non-ff)` — branch has diverged from the remote

Someone (or another agent) pushed to the same branch. The script **never pushes with `--force`**. You have to decide manually:

```powershell
cd "C:\Users\Administrator\Desktop\Personal Jarvis"
git checkout <branch>
git pull --rebase   # or: git merge origin/<branch>
```

After that the next push cycle runs through again.

### 3. `main divergiert von origin/main` — main local & remote diverge

Same fix as (2), but with extra care. The backup tag `safety/eod-main-*` is already set anyway — so you can't break anything.

---

## Note: the working tree must be clean

If you still have uncommitted changes, **the script aborts** (log entry `SKIP: Working tree dirty`). This is intentional: otherwise you'd think everything was pushed, but the open changes would not be in the backup. Before going to bed, check `git status` once and commit or stash everything.

**Exception — volatile telemetry:** files that the app rewrites on every start (currently `desktop-ttu-latest.json`) never count as dirty, otherwise the backup would skip forever on any machine that ran the app that day. The allowlist lives in the script and mirrors `DIRTY_ALLOWLIST` in `scripts/ci/check_release_completeness.py`.

---

## Clarification: who pushes, and when

Coding agents on the **shared primary checkout** push after each completed commit (`git pull --rebase --ff-only` if origin moved, then `git push`). Never `--force`, never `--no-verify`.

**Linked worktrees do not push** — mission workers and isolated agent worktrees commit locally; the parent on the primary checkout lands that work. The evening script is the backstop for a crash between commit and push, and for any branch the live path did not reach.

The Task Scheduler starts this script as a standalone Windows program (`powershell.exe`), not inside a worker harness, so a worker-tool deny on `git push` does not block the backup.
