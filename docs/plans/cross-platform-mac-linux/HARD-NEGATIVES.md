# Cross-Platform Port — HARD NEGATIVES (HN-1 .. HN-18)

> **Inviolable rules. Every coding-agent on this migration MUST inline-quote the
> HN-numbers relevant to its wave at the top of its work.** These are derived
> directly from the frozen Architecture Decisions in
> [`_FROZEN-DECISIONS.md`](./_FROZEN-DECISIONS.md) (AD-1..AD-15) and the house
> doctrine in `CLAUDE.md` (AP-1..AP-18) + `docs/BUGS.md`. A hard negative is not
> a guideline you weigh against convenience — it is a wall. If a task seems to
> require breaking one, **stop and split the task**, do not break the rule.
>
> Each rule: a short imperative + one-line *why*. On conflict with anything else,
> the frozen ADs win.

---

## Coexistence & the closed-bug graveyard

- **HN-1 — Never rewrite, refactor, or "clean up" a Windows implementation. Add a sibling behind the seam.**
  *Why:* AD-7 is permanent additive coexistence. BUG-009 (wake threshold `0.15`), BUG-012 (`NO_WINDOW_CREATIONFLAGS` at 16 sites), BUG-014 (WDM-KS audio filter), BUG-030 (LWA-chroma overlay re-apply) all live *inside* those impls. Touching them re-opens closed bugs.

- **HN-2 — Never delete, lower, or "simplify" a regression guard that pins a Windows fix.**
  *Why:* The wake-threshold floor-guard, the `-transparentcolor` click-through test, the audio host-API filter test are the only things standing between you and a four-times-recurring bug class. Reverse-patching a test to make a port "pass" drives the root cause back into production (`docs/BUGS.md` Lebensgrundlage clause).

- **HN-3 — Never branch on `sys.platform` inline in a consumer. Read the capability from the shared `jarvis/platform/` module.**
  *Why:* AD-5 mandates one cached, frozen `Capabilities` source of truth. Re-detecting platform per call-site is exactly the multi-source drift that produced BUG-008 (four episodes) and the Sidebar `isValidSection` drift (Bug UI-1).

---

## Always degrade, never raise, never lie

- **HN-4 — No `sys.platform` factory branch may EVER raise. An unavailable capability logs one clear English message and returns a null/no-op implementation.**
  *Why:* AD-6 + AD-OE6 "zero silent drops" and the always-degrade contract. A raising branch on macOS/Linux is the cross-platform equivalent of the silent-pipeline-init-crash that left "Hey Jarvis" mute (`project_bug_voice_silent_pipeline_init_swallow`).

- **HN-5 — Never silently return an empty result to hide a fixable misconfiguration. Detect-and-degrade means: probe → if missing, log an onboarding message AND fall back — never silently empty, never hard-block.**
  *Why:* AD-13. A silently-empty AX/AT-SPI tree recreates the "said it worked, nothing happened" class (BUG-002 silent vision, BUG-003 silent SAPI5 fallback). Hard-blocking instead breaks the always-degrade contract.

- **HN-6 — Never claim a GUI/permission feature "works" without a live sign-off. CI-green is NOT sufficient for Orb transparency, global-hotkey capture, the AX/AT-SPI tree, or the elevation prompt.**
  *Why:* AD-3. These paths cannot be proven by headless CI. Docs carry an honest per-feature badge: `live-verified <date> on <device>` or `unverified-on-real-desktop` (EK-5). Stating "done" without the badge is a false success claim.

---

## Import cleanliness (the CI gate that must stay green)

- **HN-7 — Never add a module-scope import of a Windows-only package. Lazy + guarded inside the function/branch only.**
  *Why:* AD-4 + EK-6. The import-cleanliness gate runs `python -c "import jarvis"` on Linux/macOS and fails the matrix if any module-scope `pywin32`/`winreg`/`global_hotkeys`/`pywinpty`/`pywinauto` import exists. Mirror the existing guarded pattern (`pty_manager.py:71`, `ipc.py:99-110`).

- **HN-8 — Never add `pyobjc-*`, `pyatspi`, or `pynput` to the base `dependencies`. Extras only.**
  *Why:* AD-14 + cloud-first doctrine. `pynput`+`ptyprocess` → `[desktop]` (no marker; `ptyprocess` marked `sys_platform != 'win32'`). `pyobjc-framework-{Quartz,ApplicationServices,Accessibility}` → `[desktop-macos]` (`sys_platform == 'darwin'`). The €5-VPS `python:3.11-slim` install must stay clean.

- **HN-9 — Never put `pyatspi` in a pip extra. It is GObject-Introspection, distro-packaged only (`apt install python3-pyatspi gir1.2-atspi-2.0`).**
  *Why:* AD-14. `pyatspi` is **not on PyPI** — adding it to `pyproject.toml` breaks `pip install` on every box. It is a documented system prerequisite gated behind a `capabilities.has_ax_tree` runtime probe.

- **HN-10 — Never add a new Windows-only dependency without the `; sys_platform == 'win32'` marker, mirroring `pyproject.toml:99-110`.**
  *Why:* AD-14. An unmarked Windows package poisons the Linux/macOS install and the import gate.

---

## Admin / elevation (the highest-risk surface)

- **HN-11 — Never use `shell=True` and never pass unvalidated argv anywhere on the elevation path.**
  *Why:* AD-12. The transport-free HMAC/envelope/Pydantic-argv layer (`ipc.py:65-262`) is the security core; pattern-validated argv is what makes injection impossible. `shell=True` re-introduces the exact surface the design eliminates (AP-3 territory).

- **HN-12 — Never weaken, bypass, or "make optional" the HMAC signature or the Pydantic discriminated-union argv validation to add a feature or convenience.**
  *Why:* AD-12 + the resolved security-vs-convenience conflict. HMAC covers nonce|timestamp|op_type|op_id|args (`ipc.py:76-85`); every new per-OS op (`brew`/`launchctl`/`apt`/`systemctl`/`ufw`/`pkexec`) extends the same `AdminOperation` union (`schema.py:175-209`), it does not get a side door.

- **HN-13 — Never drop the peer-credential check on the Unix transport. `SO_PEERCRED`/`LOCAL_PEERCRED` on a `0700` socket in `$XDG_RUNTIME_DIR` is the moral equivalent of the Windows SDDL-ACL pipe.**
  *Why:* AD-12. Without peer-cred any local process can drive the privileged helper.

- **HN-14 — `NullElevator` is the default. A headless / no-auth box refuses elevation with a clear English message; it never silently runs privileged ops.**
  *Why:* AD-12. Interactive auth is never CI-testable end-to-end, so the safe default must be refusal, not a guessed elevation path.

---

## CI matrix discipline

- **HN-15 — Never merge a wave before its CI matrix (`ubuntu-latest` + `macos-latest` + `windows-latest`) is green for `ruff` + `mypy` + `pytest -m "not skip_ci"`.**
  *Why:* AD-4 + EK-1. The matrix is Wave 0, a hard prerequisite; nothing else merges until it is green. Copy the 3-OS shape from `.github/workflows/cross-runner-hash.yml:76-86`.

- **HN-16 — Never let a mass-skip pass as green. The minimum-passed-count floor must hold; a collapse in collected/passed tests is a regression, not a pass.**
  *Why:* AD-4. A mass-skip (collection error, import shim swallowing a whole module) shows zero failures while testing nothing — the floor is the only thing that catches it.

---

## Process hygiene (carried from CLAUDE.md, non-negotiable here)

- **HN-17 — Every artifact is English; never `git push --force`; never `--no-verify`; never bypass signing.**
  *Why:* CLAUDE.md Output-Language Policy + git policy. Force-push on a shared migration branch destroys parallel-worktree work; `--no-verify` skips the hooks that guard the tree.

- **HN-18 — Every new subprocess on ANY OS passes `creationflags`/equivalent from `jarvis/core/process_utils.py`; the Windows-cursor swap (`SetSystemCursor`) and other no-OS-equivalent power-user bits stay Windows-only no-ops off Windows.**
  *Why:* AP-1 (BUG-012 flicker storm) is Windows-specific but the discipline of routing every subprocess through one helper is universal; AD-11 keeps `SetSystemCursor` Windows-only because there is no cross-platform equivalent — do not fabricate one.
