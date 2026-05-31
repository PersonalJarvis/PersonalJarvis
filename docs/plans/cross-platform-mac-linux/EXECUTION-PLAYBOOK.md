# Cross-Platform Port — EXECUTION PLAYBOOK (operator manual)

> The cockpit manual for a **non-coder maintainer**. You never type a PowerShell
> command, a `git` command, or edit a file by hand. **Every action is a paste**:
> you copy a prompt out of [`PHASE-TRACKER.html`](PHASE-TRACKER.html) (or
> [`PROMPTS.md`](PROMPTS.md)) into a fresh Claude Code session, and the coding
> agent does the typing. Your job is to *route* prompts, *watch* the acceptance
> commands go green, and *paste the merge prompt* when a wave is done.
>
> Canonical decisions live in [`_FROZEN-DECISIONS.md`](_FROZEN-DECISIONS.md)
> (AD-1..AD-15, EK-1..EK-6). The inviolable rules are
> [`HARD-NEGATIVES.md`](HARD-NEGATIVES.md) (HN-1..HN-18) and the per-wave sub-task
> IDs live in the five [`WELLE-0..4`](WELLE-0-foundation.md) briefs. This playbook
> tells you *how to drive* them; it never restates the engineering decisions.
> Output language: English (repo `CLAUDE.md` Output-Language Policy).

---

## How to use this cockpit

1. **Open the cockpit.** Double-click [`PHASE-TRACKER.html`](PHASE-TRACKER.html).
   It is a self-contained file — no install, no server, no internet. It opens in
   any browser and remembers your progress in that browser (localStorage), so you
   can close it and reopen it without losing your place.

2. **Pick a wave tab.** The cockpit has one tab per wave: **Wave 0 → Wave 4**.
   Start at **Wave 0** — it is the *blocking foundation* and **nothing else may
   merge until its CI matrix is green** (AD-4 / HN-15). The later tabs stay locked
   in your head until Wave 0's matrix is green.

3. **Read the wave's terminal cards.** Each wave tab shows one card per
   coding-agent session you need to run. A card carries: a **title** (the feature
   + the sub-task IDs it covers, e.g. "Terminal · 1.1 + 1.2"), a **Copy prompt**
   button, and a **checklist of acceptance commands** the agent must make green.

4. **For each card, open a fresh Claude Code session and paste the prompt.**
   Click **Copy prompt** on the card, open a brand-new Claude Code window (one per
   card — see "How many sessions" per wave below), and paste. The agent reads the
   prompt, opens its own isolated worktree, writes code, and runs the acceptance
   commands itself. You do not type anything.

5. **Watch the acceptance commands go green.** The agent will report each
   acceptance command from the matching `WELLE-*.md` sub-task (for example
   `pytest tests/unit/terminal/test_unix_pty.py -v` green). When all the card's
   checklist items are green, tick the card's checkbox in the cockpit. **Green
   acceptance commands in the agent's own report are what you trust** — not the
   agent's prose summary.

6. **Run the phase-check prompt.** When every card in a wave is ticked, copy the
   wave's **Phase-check** prompt (also in the cockpit / `PROMPTS.md`) into a fresh
   session. It re-runs the whole wave's acceptance set in one place and tells you,
   in plain language, whether the wave is truly done or which card regressed.

7. **Paste the merge prompt.** Only after the phase-check is green: copy the
   wave's **Merge** prompt into a fresh session. It merges the wave's worktrees in
   the documented order, re-confirms CI is green, and reports the live remote hash
   so you have proof the work actually landed. Then move to the next wave tab.

**Golden rule:** the cockpit is the source of truth for *what to paste next*; the
`WELLE-*.md` briefs are the source of truth for *what each sub-task ID means and
what "green" looks like*; this playbook is the source of truth for *the ritual*.
You never leave the paste-only loop.

---

## Per-wave operator ritual

Each wave below lists: how many parallel Claude Code sessions (one per worktree)
to open, which sub-task IDs go into which session, the order to run them, what to
do *between* agents, when to run the phase-check, and when to run the merge. The
sub-task IDs (e.g. `1.1`, `2.4`, `3.6`) are the ones frozen in the `WELLE-*.md`
briefs — paste the prompt for that ID, watch its acceptance criteria go green.

### Wave 0 — Foundation (BLOCKING) · brief: [`WELLE-0-foundation.md`](WELLE-0-foundation.md)

**This wave gates everything. Do it first, alone, and do not start Wave 1 until
its merge prompt has reported the CI matrix green (AD-4 / HN-15).**

- **Sessions to open: 3 parallel worktrees**, but merged in a strict order at the
  end.
  - **Session 0-A — Platform module:** paste the card covering **0.1** (platform
    detector + `Capabilities` dataclass) **+ 0.2** (capability probes). One
    worktree — these two are tightly coupled.
  - **Session 0-B — Orb-conflict resolution:** paste the card covering **0.6**
    (record the Tk-vs-PySide6 orb verdict as `ADR-orb-framework.md`) **+ 0.7**
    (clean the stale `ui.orb` / `OS-Level/src` conftest references).
  - **Session 0-C — CI matrix:** paste the card covering **0.3** (the GitHub
    Actions Win+Mac+Linux matrix) **+ 0.4** (import-cleanliness gate) **+ 0.5**
    (minimum-passed-count floor). One worktree — `ci.yml` and its two gate scripts
    ship together.
- **Order + between-agent steps:** 0-A and 0-B can run **at the same time** (no
  shared files). 0-C references the work of both (its matrix runs the new
  `tests/unit/platform/` tests from 0-A and needs the clean collection from 0-B),
  so **start 0-C last**, or let it rebase after 0-A and 0-B report green. Between
  agents you do nothing but watch their acceptance commands; if one finishes
  early, tick its card and wait for the others.
- **Phase-check:** once all three cards are ticked, paste the **Wave 0
  phase-check** prompt. It confirms `import jarvis` is clean, the platform module
  caches, and the new tests collect.
- **Merge:** paste the **Wave 0 merge** prompt. Per the brief's merge order it
  lands **platform module (0-A) → conftest cleanup (0-B) → CI workflow (0-C)
  last** (0-C gates on the others). The merge prompt then watches the CI matrix
  with `gh pr checks --watch` and reports green for all three OS legs. **Until
  this is green, Wave 1 is forbidden.**

### Wave 1 — Easy / CI-provable · brief: [`WELLE-1-easy-ports.md`](WELLE-1-easy-ports.md)

- **Sessions to open: 3 parallel worktrees, fully independent** (no shared
  feature files):
  - **Session 1-A — Terminal:** paste the card covering **1.1** (`PtyBackend`
    protocol + `UnixPtyBackend` via `ptyprocess`) **+ 1.2** (Unix shell discovery).
  - **Session 1-B — App-launch:** paste the card covering **1.3** (cross-platform
    `resolve_app_launch_target` + platform-conditional `KNOWN_APPS`).
  - **Session 1-C — Hotkey:** paste the card covering **1.4** (`HotkeyBackend`
    protocol + pynput / global-hotkeys / noop backends).
- **The one ordering catch — sub-task 1.5 (pyproject extras):** `1.5` adds both
  `ptyprocess` (Terminal) and `pynput` (Hotkey) to the `[desktop]` extras, so it
  is touched by two worktrees. To avoid a merge conflict on `pyproject.toml`,
  paste the tiny **1.5** prompt **first as its own quick session**, let it merge,
  then start 1-A/1-B/1-C. (The cockpit card for 1.5 is marked "do first".)
- **Between-agent steps:** none — the three feature sessions never touch the same
  file once 1.5 has landed. Run all three in parallel; tick each card as its
  acceptance commands go green (Terminal's `test_unix_pty.py` real-PTY test is the
  headline win — it proves a real shell end-to-end on the runners, EK-4).
- **Phase-check:** paste the **Wave 1 phase-check** when 1.5 + all three feature
  cards are ticked. It re-runs the terminal/app-launch/hotkey acceptance sets on
  the matrix.
- **Merge:** paste the **Wave 1 merge** prompt. It merges the three feature
  worktrees (1.5 already merged), re-confirms the matrix green, and reports the
  remote hash.

### Wave 2 — Permission / GUI-heavy · brief: [`WELLE-2-gui-permission.md`](WELLE-2-gui-permission.md)

- **Sessions to open: 2 parallel worktrees:**
  - **Session 2-D — UI-element-click:** paste the card covering **2.3** (the
    AX/AT-SPI → canonical-UIA `role_map.py` — *do this first within the session*,
    2.1/2.2 build on it), then **2.1** (macOS AX-tree `VisionSource`), **2.2**
    (Linux AT-SPI `VisionSource`), **2.4** (the `tree_factory` + rewiring the 6
    hardcoded `UIATreeSource()` call sites). These are coupled — one worktree.
  - **Session 2-E — Orb:** paste the card covering **2.5** (`OverlaySurface`
    protocol + `TkColorKeyOverlay` for Win+Mac) **+ 2.6** (Linux best-effort
    surface + `TrayOnlySurface` fallback) **+ the `desktop-macos` half of 2.7**.
- **The shared file — sub-task 2.7 (pyproject extras):** split it. **Session 2-E
  owns the `desktop-macos` block** in `pyproject.toml`; **Session 2-D only adds
  the one-line `pyatspi`-is-distro-only note in the README** (never touches
  pyproject). So there is no pyproject conflict between the two sessions.
- **Between-agent steps:** the two sessions are independent — run in parallel.
  Reminder for both: this is the wave where CI proves *logic only*. The visible
  behavior (a real AX tree, a transparent orb) is **not** confirmed here — it is
  deferred to the Wave 4 live sign-off (AD-3 / HN-6). Do not let an agent claim the
  orb "works" on Mac/Linux at this stage; the honest status is "CI-green logic,
  live-unverified."
- **Phase-check:** paste the **Wave 2 phase-check** once both cards are ticked. It
  confirms the 6 former `UIATreeSource()` call sites now go through the factory and
  the new vision/overlay tests are green.
- **Merge:** paste the **Wave 2 merge** prompt (Worktree D then E, or either order
  — they share no production file).

### Wave 3 — Admin / elevation (security-sensitive) · brief: [`WELLE-3-admin.md`](WELLE-3-admin.md)

- **Sessions to open: 2 worktrees, but Worktree F is internally sequential.**
  - **Session 3-F — Transport + elevation (security core):** paste the card that
    runs **3.1 → 3.2 → 3.4 → 3.6 in that order**. 3.1 (extract the `AdminTransport`
    seam, keep the HMAC core untouched) **must land first**; then 3.2
    (`UnixSocketTransport` + peer-cred) and 3.4 (`Elevator` + `NullElevator`) build
    on it; 3.6 wires them into `AdminClient` + the helper last. This is one
    session working through the chain in order.
  - **Session 3-G — Op vocabulary + ADR:** paste the card covering **3.3** (per-OS
    op vocabulary: macOS `brew`/`launchctl`, Linux `apt`/`systemctl`/`ufw`) **+
    3.5** (write ADR-0020, mark ADR-0001 superseded). Independent of the transport
    seam — runs fully in parallel with 3-F.
- **Between-agent steps — extra care, this is the highest-risk surface:** after
  Session 3-F's agent reports green, paste the **security-review** prompt (it asks
  the agent to run a `requesting-code-review` pass focused on the
  no-`shell=True` / pattern-validated-argv / peer-cred invariants — HN-11/HN-12/
  HN-13). Do this **before** the merge prompt for this wave. Do the same for any
  PR in 3-G that adds an op.
- **Phase-check:** paste the **Wave 3 phase-check** once both cards are ticked. It
  re-runs the admin unit + loopback tests and confirms `ipc.py`/`client.py`/
  `helper.py` import clean on Linux/macOS.
- **Merge:** paste the **Wave 3 merge** prompt (3-G's ADR + ops and 3-F's seams
  can merge in either order; the brief notes no shared files between them).

### Wave 4 — Hardening + sign-off · brief: [`WELLE-4-hardening.md`](WELLE-4-hardening.md)

**Mostly serial and human-gated. The bottleneck is hardware access, not code.**

- **Sessions to open: 1 main sequential worktree + 1 side-track.**
  - **Session 4-main — sequential:** paste the cards in order: **4.1** (build the
    live sign-off checklist + `signoff_probe.py`) → **4.2** (run the manual
    sign-off on a real macOS box and a real Linux desktop, record the verdicts in
    `SIGNOFF-LOG.md`) → **4.3** (write honest per-feature labels into the README
    capability matrix) **+ 4.4** (update CLAUDE.md cross-platform status) → **4.6**
    (close-out: confirm EK-1..EK-6 with evidence links).
  - **Session 4-side — JARVIS-20 benchmark:** paste the **4.5** card — it *runs*
    the 20-scenario benchmark from
    [`JARVIS-20-CROSSPLATFORM.md`](JARVIS-20-CROSSPLATFORM.md) and records the
    per-OS scores in `JARVIS-20-RESULTS.md`. It can run in parallel with 4.3/4.4
    once 4.2's devices are available (it shares the hardware session).
- **The live-sign-off step (4.2 + 4.5) is the one place you do something physical
  — see "Verification reality" below for exactly how a non-coder gets a Mac and a
  Linux desktop.** The agent brackets each manual action and tells you what to
  observe; you confirm PASS / FAIL / unverified.
- **Honest-label rule:** any GUI/permission behavior you could not reach on real
  hardware is recorded as `unverified-on-real-desktop` with the reason — **that is
  a valid, shipping outcome** (AD-3 / HN-6), not a failure. Never let the agent
  upgrade an `unverified` to "verified" without a dated device.
- **Phase-check + close-out:** 4.6 *is* the close-out — it re-confirms the CI
  matrix is still green after the doc-only changes and ticks EK-1..EK-6 with
  evidence pointers. There is no separate merge prompt beyond the standard
  per-card merge, because Wave 4 is docs + the sign-off log only (no production
  code).

---

## Verification reality (operator note — read before you trust a green check)

This is the most important section for a non-coder. **A green CI run does not mean
a GUI feature works on a real desktop.** The plan is honest about this (AD-3 /
HN-6), and so must you be when you tell anyone "Jarvis runs on Mac now."

There are two kinds of features in this port, and you confirm them in two
different ways:

### Kind 1 — features you can trust from green CI alone (no hardware needed)

You can confidently say these work on Mac and Linux **just by seeing the Wave 1
CI matrix go green** — the runners are real Linux and real macOS machines, and the
logic is fully exercised there:

- **Terminal** — the runner spawns a *real* shell through a real PTY and checks the
  output round-trips. This is the standout win: **fully CI-verified, no live
  sign-off ever needed** (EK-4). Green = done.
- **App-launch resolution** — the runner proves the name-to-launch-target logic
  (e.g. "Safari" resolves to `open -a Safari`). The *actual* launch is a light
  live check, but the brains of it are CI-proven.
- **Hotkey logic, app-launch logic** — the registration/refcount/degrade logic and
  the platform factory selection are CI-proven on the runners.

For Kind 1, your ritual is simply: watch the Wave 1 merge prompt report the matrix
green, and tick it. No Mac, no Linux box, nothing physical to do.

### Kind 2 — features that need the one-time live sign-off (real hardware, once)

These four behaviors **cannot** be proven on a headless runner, because they need a
screen, a permission grant, or an interactive prompt. CI proves their *logic*; only
a one-time live sign-off (Wave 4, AD-3) proves the *visible behavior*:

- **Orb overlay** — that the orb is actually *transparent* (not an opaque magenta
  box) needs a real display.
- **UI-element-click (AX / AT-SPI tree)** — capturing a real accessibility tree
  needs a granted macOS Accessibility permission / a running Linux AT-SPI bus.
- **Hotkey live capture** — that keys actually *arrive* from the OS (vs just
  registering without error) needs a real keyboard session.
- **Admin / elevation prompt** — the UAC / polkit / Touch-ID password sheet is
  interactive by design and can never run on a runner.

**How a non-coder gets the one-time sign-off (you have three honest options, pick
per feature):**

1. **Use the project's own €5 Linux VPS — you already have it.** The Hetzner CX22
   the whole project runs on is a *real Linux box*. Paste the Wave 4 `signoff_probe`
   prompt aimed at it and the agent will SSH-drive the headless-Linux paths for you:
   it confirms the terminal, app-launch resolution, the capability probes, and —
   crucially — the *degrade* behaviors (AT-SPI-bus-absent → pixel fallback,
   no-display → tray-only orb, Wayland → hotkey no-op). This covers the **Linux
   degrade half** of the sign-off without you touching a keyboard on a desktop.
   *What it cannot do:* the GUI-present Linux path (a headless VPS has no
   compositor), so the *transparent* orb on Linux stays `unverified-on-real-desktop`
   unless you also do option 2 or 3.

2. **Borrow or rent a Mac for an hour.** You do not need to own one. The
   `macos-latest` CI runner already covers macOS *logic* + the terminal. For the
   four GUI/permission behaviors on Mac you need a real Mac *once*: borrow a
   friend's, use a library/office machine, or **rent a cloud Mac by the hour**
   (e.g. MacStadium / a "Mac mini in the cloud" hourly service). Open Claude Code on
   it, paste the Wave 4 `signoff_probe --feature ax` / `--feature orb` /
   `--feature hotkey` / `--feature admin` prompts, follow the on-screen "grant this
   permission, then watch for X" steps, and tell the agent PASS or FAIL. One hour,
   once, done — the agent writes the dated `live-verified … on Mac mini (rented)`
   line for you.

3. **Borrow a Linux desktop with a screen** (any friend's Ubuntu/Fedora laptop) for
   the GUI-present Linux orb + AT-SPI tree + X11 hotkey capture. Same ritual: paste
   the probe prompts, follow the steps, confirm.

4. **Or honestly label it `unverified-on-real-desktop`.** If you cannot reach a
   device, that is a *valid shipping outcome* under AD-3 / HN-6. The agent records
   the label and the reason; the plan still ships. **Never** let yourself (or an
   agent) claim a Kind-2 feature is "verified" without a dated device line in
   `SIGNOFF-LOG.md`. A false "it works" is the exact failure this whole
   verification ladder exists to prevent.

**Bottom line for the operator:** Terminal + app-launch + hotkey-logic = trust the
green CI (Kind 1). Orb + UI-element-click + hotkey-capture + elevation = need the
one-time sign-off (Kind 2) via the VPS for Linux-degrade, a rented/borrowed Mac for
macOS, and a borrowed Linux desktop for the GUI-present Linux paths — or an honest
`unverified` label. The per-feature badge in `SIGNOFF-LOG.md` and the README
capability matrix must always tell that truth.

---

## Recovery (when something goes wrong, paste a recovery prompt)

You never debug by hand. Every failure mode has a ready-made recovery prompt in
[`PROMPTS.md`](PROMPTS.md) (and a matching button in the cockpit). Match your
symptom to the prompt and paste it into a fresh session:

- **An agent stalls or says it is rate-limited / out of quota mid-task.** Paste the
  **"resume a rate-limited / interrupted agent"** recovery prompt. It re-points a
  fresh session at the same worktree and continues from the last green acceptance
  command — it does *not* start over and does *not* duplicate work.
- **The CI matrix goes red (a leg fails: ruff, mypy, a test, the import gate, or
  the min-passed floor).** Paste the **"red CI — diagnose and fix the failing
  leg"** recovery prompt. It reads the failing GitHub Actions log, identifies which
  OS leg and which gate failed, fixes it on the wave's branch, and re-watches the
  matrix. (Most common: a module-scope Windows-only import slipped in — HN-7 — or
  the min-passed floor caught a mass-skip — HN-16.)
- **A merge conflict (two worktrees touched the same file — almost always
  `pyproject.toml` in Wave 1's 1.5 or Wave 2's 2.7).** Paste the **"resolve a
  merge conflict between two wave worktrees"** recovery prompt. It opens an
  isolated worktree, takes both additive edits (these are additive extras, never a
  rewrite — HN-1), and re-runs the acceptance set before merging.
- **A worktree is stuck / the agent can't find the right files / "works in tests
  but Jarvis behavior unchanged."** Paste the **"unstick a worktree (preflight +
  editable-install check)"** recovery prompt. It runs the mandatory
  `scripts/preflight.ps1` and confirms `import jarvis` points at the worktree clone
  (the four-layer restore trap, BUG-006/014). This is the single most common
  "nothing changed after I ran it" cause.

If a symptom doesn't match any of the four, paste the **generic "diagnose this and
report back in plain language"** prompt from `PROMPTS.md` — it asks the agent to
explain what it sees and propose the next paste, without changing anything until
you approve.

---

## The one rule that makes this work

**You never type a shell command.** Not `git`, not `pwsh`, not `pip`, not
`pytest`. Everything — opening a worktree, running tests, merging, reading a CI
log, fixing a conflict — is done by a coding agent reacting to a prompt you pasted.
The cockpit tells you which prompt to paste next; the agent's green acceptance
commands tell you when to move on; the merge prompt's reported remote hash is your
proof the work landed. If you ever feel the urge to type a command, stop: there is
a prompt for that in [`PROMPTS.md`](PROMPTS.md). Paste it instead.
