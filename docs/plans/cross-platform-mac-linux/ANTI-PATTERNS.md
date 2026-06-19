# Cross-Platform Port — ANTI-PATTERNS (AP-1 .. AP-13)

> Traps specific to **this** macOS/Linux migration. Each entry: **the trap**, **how
> it reproduces**, **the counter-pattern**. Style and severity follow `CLAUDE.md`'s
> AP-table and the `docs/BUGS.md` recurring-bug classes. These are *migration*
> anti-patterns — the inviolable rules live in
> [`HARD-NEGATIVES.md`](./HARD-NEGATIVES.md) (HN-1..HN-18); the frozen ADs live in
> [`_FROZEN-DECISIONS.md`](./_FROZEN-DECISIONS.md).
>
> Numbering here is **local to this file** (AP-CP-style). It does NOT extend the
> AP-1..AP-18 table in `CLAUDE.md` — quote them as "ANTI-PATTERNS.md AP-N".

| # | If you do this... | ...you get this bug |
|---|---|---|
| AP-1 | Pipe `ptyprocess` `read()` straight into the str path (or `write()` a `str`) | Garbled / crashing terminal — str↔bytes mismatch at the PtyBackend seam |
| AP-2 | Treat an empty macOS AX tree as "nothing clickable" | Silent dead feature hiding an ungranted Accessibility permission |
| AP-3 | `pip install pyatspi` / put it in `pyproject.toml` | Linux install breaks — `pyatspi` is apt-only |
| AP-4 | Register a Wayland global hotkey like on X11 | Hotkey silently never fires; user thinks Jarvis is deaf |
| AP-5 | Call `wm_attributes("-transparentcolor", …)` on X11 Tk | `TclError` at orb startup → crash instead of degrade |
| AP-6 | Let a mass-skip / collection error read as green | A whole feature regresses invisibly under a "passing" matrix |
| AP-7 | Port the 13 Windows admin ops literally via `sudo` | Nonsensical helper (`sudo winget`, `sudo sc`) — they have no Unix analogue |
| AP-8 | Emit raw AX/AT-SPI role strings into the model prompt | Role-vocabulary drift — the BUG-008 multi-layer-enum-drift class |
| AP-9 | Test Orb/AX/hotkey with a real display in CI | Flaky matrix + still no real-hardware proof |
| AP-10 | Shell a privileged command string | Re-introduces the injection surface the HMAC design eliminated |
| AP-11 | Re-detect `sys.platform` per call-site | Capability drift; AD-5 single-source bypassed |
| AP-12 | Ship a port with no `tests/fakes/` fake (use `unittest.mock`) | Violates EK-3; untestable seam, no regression guard |
| AP-13 | Hard-import the Windows package "just at the top of the new file" | Import-cleanliness gate goes red on Linux/macOS |

---

## AP-1 — str-vs-bytes mismatch at the PtyBackend seam

**The trap.** `pywinpty`'s `PtyProcess.read()` returns a **`str`** (already decoded);
`ptyprocess.PtyProcess.read()` returns **`bytes`**. Likewise `write()` wants `str`
on winpty and `bytes` on ptyprocess. If the Unix backend feeds bytes into a path
that assumes str (or vice-versa), the reader thread either crashes or ships
mojibake to the xterm.js frontend.

**How it reproduces.** The current reader loop already keeps a defensive
both-ways decode — `pty_manager.py:210-214`:

```python
# pywinpty liefert str (bereits dekodiert). Falls doch bytes:
if isinstance(data, bytes):
    text = data.decode("utf-8", errors="replace")
else:
    text = str(data)
```

That `isinstance` branch is the **only** reason a naive Unix swap doesn't crash on
read. The *write* path (and `spawn`/`setwinsize` dimension order) has no such
guard, so a wave-1 author who wires `ptyprocess` in and writes `str` keystrokes
gets a `TypeError: a bytes-like object is required`.

**Counter-pattern.** Normalize **once, at the backend seam** (AD-9): the Unix
`PtyBackend.read()` decodes bytes→str before handing up; `write()` encodes str→bytes
before handing down; `spawn` honors ptyprocess `dimensions=(rows, cols)`. Above the
seam, the daemon-thread read-loop and the frontend stay byte-agnostic. Pin it with a
fake-PTY contract test that asserts the up-seam type is always `str` on both backends.

---

## AP-2 — macOS AX tree silently empty because Accessibility was never granted

**The trap.** On macOS, `AXUIElementCopyAttributeValue` returns an empty tree (not
an error) when the app lacks the **Accessibility** / **Input-Monitoring** TCC grant.
The natural code reaction — "tree is empty, nothing to click, return `[]`" — hides a
one-checkbox fix from the user and recreates the "said it worked, nothing happened"
class (BUG-002 silent vision, BUG-003 silent SAPI5).

**How it reproduces.** First run on a fresh Mac, permission un-granted. The
`VisionSource` returns zero `UIANode`s; the click path falls through to pixel-click
or to "no element found"; the user hears a plausible answer but nothing is clicked,
and there is **no log line telling them to open System Settings → Privacy**.

**Counter-pattern.** Detect-and-degrade per AD-13 + HN-5. Distinguish *empty* from
*forbidden*: probe `AXIsProcessTrusted()` (cache the verdict in
`capabilities.ax_permission_granted`); on `False`, log **one** clear English
onboarding line (e.g. "Accessibility permission not granted — enable Jarvis in
System Settings → Privacy & Security → Accessibility to use UI-element clicking;
falling back to pixel-click.") and then degrade to the already-working pixel path.
Never silently empty; never hard-block.

---

## AP-3 — assuming Linux AT-SPI is pip-installable

**The trap.** Adding `pyatspi` to `pyproject.toml` (base or any pip extra) because
"it's the Linux AX lib." `pyatspi` is GObject-Introspection — it is **not on PyPI**.
`pip install` then fails, taking the whole `pip install -e ".[desktop]"` down with it,
including on the CI Linux runner.

**How it reproduces.** Author lists `pyatspi` under `[desktop]` or a new
`[desktop-linux]` extra. `pip install` errors with `No matching distribution found
for pyatspi`; the Wave-0 matrix (HN-15) goes red on `ubuntu-latest`; install also
breaks for any real Linux user.

**Counter-pattern.** AD-14 + HN-9: `pyatspi` is a **documented system prerequisite**
(`apt install python3-pyatspi gir1.2-atspi-2.0`), gated behind a
`capabilities.has_ax_tree` runtime probe (lazy `import pyatspi` inside a try/except
that flips the capability). The pip extras carry `pynput`/`ptyprocess`
(`[desktop]`) and `pyobjc-*` (`[desktop-macos]`) — never the AT-SPI bindings.

---

## AP-4 — assuming a Wayland global hotkey works like X11

**The trap.** Treating `pynput`'s global hotkey listener as "works on Linux" without
distinguishing X11 from Wayland. Wayland **forbids** global keyboard capture by
security design — the listener registers without error and then receives **zero
events**. The hotkey looks wired but is dead.

**How it reproduces.** Author tests on an X11 session (or in a headless runner that
reports neither), ships "Linux hotkey done." A Wayland user (GNOME/KDE default on
modern distros) presses the combo, nothing happens, no error, no log.

**Counter-pattern.** AD-8 + HN-4. The `jarvis/platform/` capability sets
`is_wayland` (check `XDG_SESSION_TYPE` / `WAYLAND_DISPLAY`). On Wayland: do **not**
register; log "Global hotkey unavailable on Wayland by OS design — use the wake-word
to summon Jarvis" and no-op. Mirror the macOS "registered but zero events → guide to
grant Input-Monitoring" detection. The hotkey is best-effort everywhere except
Windows; the wake-word is the universal fallback.

---

## AP-5 — assuming `-transparentcolor` exists on X11 Tk

**The trap.** Reusing the Windows/macOS color-key overlay path on Linux. Tk's
`wm_attributes("-transparentcolor", …)` is a **Windows/macOS-only** attribute; on X11
Tk it raises `TclError`. The orb then crashes at construction instead of degrading.

**How it reproduces.** The live orb uses color-key transparency at three sites —
`ui/orb/overlay.py:966`, `overlay.py:1331`, `ui/orb/virtual_cursor_window.py:286`:

```python
top.wm_attributes("-transparentcolor", COLOR_KEY_HEX)
```

Run unchanged under X11 and Tk raises `TclError: bad attribute "-transparentcolor"`.
A bare reuse turns the always-degrade orb into a hard crash.

**Counter-pattern.** AD-11 `OverlaySurface` 3-tier ladder + HN-4. `TkColorKeyOverlay`
is selected only where the attribute exists (Windows + macOS); Linux gets a
best-effort transparent surface when a compositor is present, and the
`TrayOnlySurface` fallback (driving the already-cross-platform `jarvis/ui/tray.py`
pystray tray, which carries no platform marker) when it isn't. Wrap the attribute
call in a capability check (`capabilities.has_overlay`), and on `TclError` log + fall
to the tray tier — never let it propagate. (Wave 0 must first resolve the Tk-vs-PySide6
orb-framework conflict, PC-6.)

---

## AP-6 — green-by-mass-skip CI hiding a regression

**The trap.** A "passing" matrix that actually ran almost nothing. A collection
error, a module-scope import that throws and gets swallowed by a shim, or an
over-broad `skip_ci` marker can drop the collected/passed count to a handful while
showing **zero failures** — which reads as green.

**How it reproduces.** Author adds a new OS module that import-throws on the wrong
platform; the conftest swallows it; pytest collects 12 tests instead of 1,400 and
reports "12 passed." The PR merges; the feature is untested.

**Counter-pattern.** AD-4 + HN-16: the matrix enforces a **minimum-passed-count
floor**. A drop below the floor fails the job even with zero failures. Never lower
the floor to make a red run green; never widen `skip_ci` to dodge a real failure
(echoes the Bug UI-1 "single source of truth" lesson — one allowlist, not many).

---

## AP-7 — porting the 13 Windows-native admin ops literally via `sudo`

**The trap.** Wrapping the existing op vocabulary in `sudo` to "make it Unix." The 13
ops (`schema.py:175-209`) are `install_winget`, `start_service` (sc), `add_firewall_rule`
(netsh), `read/write_registry_*` (winreg), `add_scheduled_task` (schtasks), etc. —
all Windows-native. `sudo winget`, `sudo sc`, `sudo netsh`, `sudo reg` are nonsense on
macOS/Linux.

**How it reproduces.** Author maps each Windows op string to a `sudo <samecmd>` shell
line. On Linux the helper either errors ("command not found: winget") or, worse,
shells an attacker-influenced argument with elevated rights.

**Counter-pattern.** AD-12 + PC-7: define a **new per-OS op vocabulary** as fresh
members of the same `AdminOperation` discriminated union — macOS `brew`/`launchctl`/
protected-path-via-Authorization-Services; Linux `apt`/`systemctl`/`ufw`/
protected-path-via-`pkexec`. Each op keeps Pydantic-validated argv and the HMAC
envelope (HN-11/HN-12). The op *concept* (install a package, start a service) ports;
the Windows *command strings* do not.

---

## AP-8 — role-vocabulary drift when AX/AT-SPI roles aren't normalized

**The trap.** Letting native AX roles (`AXButton`, `AXMenuItem`, `AXStaticText`) or
AT-SPI roles (`push button`, `menu item`, `label`) flow straight into the model prompt
and the click filter. Now the "clickable role" vocabulary differs per OS — the exact
multi-layer-enum-drift class that recurred four times as BUG-008.

**How it reproduces.** The Windows path filters on the canonical UIA set —
`screenshot_only_loop.py:1072`:

```python
_CLICKABLE_UIA_ROLES = frozenset({
    "Button", "MenuItem", "ListItem", "TabItem", "CheckBox", "RadioButton",
    "Hyperlink", "Edit", "ComboBox", "TreeItem", "SplitButton", "Text",
})
```

A macOS node arrives with `role="AXButton"`, fails the `not in _CLICKABLE_UIA_ROLES`
check (`:1100`), and is dropped — so the model never sees a clickable control on Mac
and silently falls back to pixel-guessing, while tests written against UIA roles still
pass. (Note: `_FROZEN-DECISIONS.md` cites `pruning.py:51`; the live source of the role
set is `screenshot_only_loop.py:1072` — normalize against whichever the seam consumes.)

**Counter-pattern.** AD-10 + the five-layer anti-drift pattern
(`docs/anti-drift-three-layer.md`). Normalize native AX/AT-SPI roles **into the
canonical UIA vocabulary** at the `VisionSource` adapter, before the `Observation`/
`UIANode` leaves the OS layer (`AXButton`/`push button` → `Button`, …). Keep a single
mapping table per OS + a parity test asserting every produced role is in the canonical
set. The prompt and the click filter stay platform-agnostic.

---

## AP-9 — testing GUI features with real displays in CI

**The trap.** Wiring the Orb / AX / hotkey-capture tests to spin up a real window or
display in the matrix. It is both flaky (no compositor on the runner) **and**
pointless — a CI display proves nothing about real-hardware GUI/permission behavior.

**How it reproduces.** Author runs an overlay test that needs an X server on
`ubuntu-latest`; it hangs or errors; author then marks it `skip_ci` everywhere to get
green — losing even the logic coverage (feeding AP-6).

**Counter-pattern.** AD-3 separates the two proofs. Logic + real-PTY → headless CI
with fakes/offscreen (the overlay suite already forces `QT_QPA_PLATFORM=offscreen`,
`tests/overlay/conftest.py:21`; `skip_ci` exists, `pyproject.toml:283`). GUI/permission
behavior → the **one-time live sign-off** on a real/borrowed/rented device (AD-3,
EK-5), recorded as a `live-verified <date> on <device>` badge. Use `tests/fakes/`
fakes for the seams (EK-3), never `unittest.mock`; never a real display in CI.

---

## AP-10 — shelling a privileged command string (re-opening the injection surface)

**The trap.** On the new Unix elevation path, assembling a privileged command as a
**string** and handing it to `sh -c` / `osascript -e "do shell script …"` /
`pkexec sh -c …`. This re-introduces the exact injection surface the Windows HMAC
design was built to eliminate.

**How it reproduces.** Author writes
`subprocess.run(f"pkexec apt install {pkg}", shell=True)`. A package name carrying
`; rm -rf ~` (or arriving via STT/chat) executes with root. The HMAC envelope and
Pydantic argv validation are bypassed because the dangerous part is a free-form string.

**Counter-pattern.** AD-12 + HN-11/HN-12. Keep the transport-free security core
(`ipc.py:65-262`): the op is a Pydantic model with pattern-validated, typed argv;
HMAC covers `nonce|timestamp|op_type|op_id|args` (`ipc.py:76-85`); the helper executes
**argv lists, never shell strings**, never `shell=True`. `pkexec`/`sudo`/`osascript`
receive a validated argv vector, and the `UnixSocketTransport` enforces the
`SO_PEERCRED`/`LOCAL_PEERCRED` peer-cred check (HN-13). `NullElevator` refuses when no
authenticated transport is present (HN-14).

---

## AP-11 — re-detecting `sys.platform` inline instead of reading the capability module

**The trap.** Sprinkling `if sys.platform == "darwin": …` across consumer sites
instead of reading the cached `Capabilities`. Two sites disagree the moment a probe
result changes, and you've rebuilt the drift that produced BUG-008 and Bug UI-1.

**How it reproduces.** The hotkey site checks `sys.platform`; the capability module
checks `is_wayland`; the orb checks `has_overlay`. On a Wayland Mac-less Linux box the
three answers diverge and the feature half-initializes.

**Counter-pattern.** AD-5 + HN-3. `detect_platform()` and the frozen `Capabilities`
dataclass are the **single source**. All six ports read `capabilities.has_hotkey`,
`has_ax_tree`, `has_overlay`, `has_pty`, `has_elevation`, `display_present`,
`is_wayland`, `ax_permission_granted` from there. The setup wizard renders one
"what works on your box" snapshot from the same object.

---

## AP-12 — shipping an OS seam with no fake and no regression guard

**The trap.** Wiring a new per-OS implementation behind the seam but testing it with
`unittest.mock` (or not at all), so there is no durable fake and no contract test.

**How it reproduces.** A `mock.patch("ptyprocess.PtyProcess")` test passes today,
silently rots when the seam contract shifts, and never catches the str/bytes
regression (AP-1) or the role-drift regression (AP-8).

**Counter-pattern.** EK-3 + house convention. Every new seam ships a `tests/fakes/`
fake (e.g. `FakePtyBackend`, `FakeAxTree`, `FakeElevatorTransport`) and unit tests
built on it — **no `unittest.mock`** (`CLAUDE.md` testing conventions). New
STT/Brain/Tool/Channel-style providers also pass the existing contract suites; new
OS seams get an analogous contract test pinning the up-seam type and the canonical
role/op vocabulary.

---

## AP-13 — the convenience module-scope import

**The trap.** "It's a macOS-only file, surely I can `import Quartz` at the top." Any
module-scope import of an OS-specific package executes during `import jarvis` on every
platform, because the package tree is imported for entry-point discovery.

**How it reproduces.** Author tops a new file with `import pyobjc` / `import pyatspi`
/ `from winpty import PtyProcess`. The import-cleanliness gate
(`python -c "import jarvis"`, AD-4) goes red on the other two OSes; the matrix blocks
the merge (or worse, a swallowed import feeds AP-6).

**Counter-pattern.** HN-7 + HN-13. Lazy-import inside the function/branch that needs
it, guarded by try/except that flips the capability, mirroring `pty_manager.py:71`
and `ipc.py:99-110`. The capability probe lives in `jarvis/platform/`; consumers read
the boolean, never the import.
