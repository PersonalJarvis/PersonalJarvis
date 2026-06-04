# End-of-Day Auto-Push for Personal Jarvis

## What does it do?

The script **automatically mirrors all local Git branches to GitHub every evening**. Before each push a **backup tag** is set (`safety/eod-<branchname>-<timestamp>`), so that even destructive follow-up actions stay reversible. If your laptop were to die tomorrow — all your work is on GitHub anyway.

Background: On 2026-05-01 a restore went well only because you instinctively set backup tags. This automation now does both (tag + push) every evening, without you having to think about it.

---

## Activate (1 command)

In PowerShell (no admin required):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<USER_HOME>\Desktop\Personal Jarvis\scripts\install-auto-push-task.ps1"
```

Default: daily at 22:00. Different time:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<USER_HOME>\Desktop\Personal Jarvis\scripts\install-auto-push-task.ps1" -Time "23:30"
```

---

## Deactivate (1 command)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<USER_HOME>\Desktop\Personal Jarvis\scripts\uninstall-auto-push-task.ps1"
```

---

## Trigger manually right now

```powershell
Start-ScheduledTask -TaskName "Personal-Jarvis-EoD-Push"
```

Or run the script directly (even without an installed task):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<USER_HOME>\Desktop\Personal Jarvis\scripts\auto-push-eod.ps1"
```

**Dry run** (only shows what would be done, pushes nothing):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<USER_HOME>\Desktop\Personal Jarvis\scripts\auto-push-eod.ps1" -DryRun
```

### Hotkey idea (optional, nice-to-have)

Place a small `.lnk` shortcut to the script on the desktop, then assign a hotkey via the Windows shortcut properties under "Shortcut key", e.g. `Ctrl+Alt+P`.

---

## Where is the log?

```
<USER_HOME>\Desktop\Personal Jarvis\logs\auto-push-eod.log
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
cd "<USER_HOME>\Desktop\Personal Jarvis"
git checkout <branch>
git pull --rebase   # or: git merge origin/<branch>
```

After that the next push cycle runs through again.

### 3. `main divergiert von origin/main` — main local & remote diverge

Same fix as (2), but with extra care. The backup tag `safety/eod-main-*` is already set anyway — so you can't break anything.

---

## Note: the working tree must be clean

If you still have uncommitted changes, **the script aborts** (log entry `SKIP: Working tree dirty`). This is intentional: otherwise you'd think everything was pushed, but the open changes would not be in the backup. Before going to bed, check `git status` once and commit or stash everything.

---

## Clarification: relationship to the OpenClaw DENY rule

In `~/.claude/settings.json` there is a `Bash(git push *)` DENY rule that prevents OpenClaw agents from pushing at runtime. **This rule does NOT affect the auto-push script**, because:

- The Task Scheduler starts the script as a standalone Windows program (`powershell.exe`).
- It does not run inside the OpenClaw harness, but as a normal user process.
- The DENY rule only filters Bash tool calls of the LLM, not external scripts.

So you keep **full control**: agents still cannot push on their own, but your own scheduled job mirrors your repo to GitHub every evening.
