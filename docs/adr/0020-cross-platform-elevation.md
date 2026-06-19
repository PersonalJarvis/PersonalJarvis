---
title: "ADR-0020: Cross-platform privileged execution (AdminTransport + Elevator seams)"
slug: adr-0020-cross-platform-elevation
diataxis: adr
status: active
owner: harald
last_reviewed: 2026-05-29
phase: 5
audience: developer
supersedes: adr-0001-ipc-named-pipe-hmac
---

# ADR-0020 — Cross-platform privileged execution: `AdminTransport` + `Elevator` seams

**Status:** Accepted (2026-05-29) — supersedes ADR-0001.
**Phase:** 5 — Admin capability (cross-platform port, Wave 3).
**Context plan:** `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` (AD-12),
`docs/plans/cross-platform-mac-linux/WELLE-3-admin.md`.

## Context

ADR-0001 chose a **Windows Named Pipe + HMAC-SHA256 + Nonce** for the IPC between
the `asInvoker` Jarvis app and the UAC-elevated admin helper, and declared the
design "not portable on Linux/Mac — not relevant: Jarvis is Windows-first." The
cloud-first doctrine (`docs/PHILOSOPHY.md`) reversed that assumption: macOS and
Linux are now **primary** runtime targets, not afterthoughts. The cross-platform
port (`_FROZEN-DECISIONS.md`) commits to porting **all six** desktop power-user
features behind clean seams, and Admin/elevation is the sixth — the largest and
most security-sensitive.

The privileged-execution path has three distinct concerns that ADR-0001 fused
into one Windows-specific blob:

1. **The security core** — canonical-args JSON, HMAC-SHA256, nonce-replay LRU,
   timestamp window, the `_decode_request` 5-step check ordering, and the
   `_AdminOpBase` (`frozen=True` + `extra="forbid"`) + pattern-validated argv +
   `shell=False` contract. This is transport- and OS-agnostic. (`ipc.py:65-262`,
   `schema.py`, `executor.py`.)
2. **The transport** — how raw signed envelopes move between the two processes.
   ADR-0001 hardcoded the Windows SDDL-ACL named pipe.
3. **The elevation mechanism** — how the helper acquires privilege. ADR-0001
   hardcoded `ShellExecuteW("runas", …)` (UAC).

Concern (1) must be **reused verbatim** on every OS — it is the injection and
replay defense and re-implementing it per OS would multiply the attack surface.
Concerns (2) and (3) are inherently OS-specific and must be swapped behind seams.

## Decision

**Split the privileged-execution path into three layers; reuse the security core
unchanged; swap transport and elevation behind two new seams (`AdminTransport`
and `Elevator`); add a per-OS op vocabulary that subclasses the same strict
schema base.**

### 1. The security core is UNCHANGED

The transport-free HMAC/envelope/Pydantic-argv layer is reused as-is on every
OS — preserve `no-shell=True`, pattern-validated argv, the nonce LRU, and the
timestamp window. Specifically unchanged:

- `_canonical_args_json`, `_compute_hmac`, `_decode_request` (with its 5-step
  ordering: parse → timestamp window → nonce-replay → HMAC verify → schema
  validate), `_encode_response`, the nonce LRU, and the
  `_TIMESTAMP_WINDOW_NS` / `_NONCE_LRU_SIZE` constants (`jarvis/admin/ipc.py`).
- `_AdminOpBase` (`frozen=True`, `extra="forbid"`) and the discriminated
  `AdminOperation` union (`jarvis/admin/schema.py`).
- The `AdminExecutor._run_subprocess` list-argv + `shell=False` +
  `NO_WINDOW_CREATIONFLAGS` contract (`jarvis/admin/executor.py`).

**The regression guard that protects this core is
`tests/unit/admin/test_hmac_replay.py`** — it exercises `_decode_request` at the
bytes level (no real pipe, no UAC) and MUST stay green across the entire Wave-3
refactor.

### 2. `AdminTransport` seam (transport swap)

A new `AdminTransport` protocol (`jarvis/admin/transport.py`) carries raw bytes:

- Server: `async def serve(handler)` where `handler: Callable[[bytes],
  Awaitable[bytes]]` is the reused `_decode_request → executor.execute →
  _encode_response` chain.
- Client: `async def roundtrip(raw: bytes) -> bytes`.

Per-OS implementations:

- **`NamedPipeTransport` (Windows)** — the relocated ADR-0001 pipe code,
  behavior-identical: pipe name `\\.\pipe\jarvis-admin-<user-SID>`, SDDL-ACL
  `D:(A;;FA;;;<SID>)` (owner-only Full Access), MESSAGE-mode read. Grandfathered
  untouched (AD-7).
- **`UnixSocketTransport` (macOS + Linux)** — an `AF_UNIX` `SOCK_STREAM` socket
  at `$XDG_RUNTIME_DIR/jarvis-admin-<uid>.sock` (fallback: a `0700` dir under
  `/run/user/<uid>/` or `tempfile.mkdtemp(mode=0o700)`); the socket file is
  `0600`, the containing dir `0700`. **The filesystem ACL replaces the Windows
  SDDL-ACL.** On accept, the peer UID is read via `SO_PEERCRED` (Linux) or
  `LOCAL_PEERCRED`/`getpeereid` (macOS) and any connection whose UID ≠ the
  server-process UID is rejected — the peer-credential check is the security
  equivalent of the SDDL owner ACE. The HMAC envelope check still runs on top:
  **defense in depth**, mirroring how the Windows transport pairs the pipe ACL
  with HMAC.

`make_admin_transport()` selects on `detect_platform()`: `win32` →
`NamedPipeTransport`; else → `UnixSocketTransport`. It never raises (AD-6).

### 3. `Elevator` seam (privilege acquisition)

A new `Elevator` protocol (`jarvis/admin/elevator.py`) spawns/authorizes the
helper bound to the transport address:

- **`UacElevator` (Windows):** the existing `ShellExecuteW("runas", …)` flow,
  unchanged (AD-7).
- **`PolkitElevator` (Linux, preferred):** `pkexec` + a polkit policy file.
- **`SudoElevator` (Linux, fallback):** `sudo` when polkit is absent.
- **`MacAuthElevator` (macOS):** `osascript … with administrator privileges`
  (or Authorization Services via pyobjc); the Touch-ID/password sheet is
  OS-driven, like UAC.
- **`NullElevator` (headless/no-auth contract):** returned when no elevation
  mechanism is available (`not capabilities.has_elevation` — e.g. a headless VPS
  with neither pkexec nor sudo). `ensure_elevated_helper` returns a refusal
  `ElevationResult` and logs the English message *"no elevation mechanism
  available on this host — privileged operations are disabled; install pkexec or
  run with sudo"*. This is the AD-6 graceful no-op: the `AdminClient.execute`
  refusal path surfaces it as a typed `AdminResponse(success=False,
  error_code=…)`, **never a crash** (AD-OE6 "zero silent drops").

`make_elevator()` selects on `detect_platform()` + capabilities; an absent
`has_elevation` always resolves to `NullElevator`.

The elevator **only spawns the helper** — it never weakens the injection
defenses for convenience. The helper still runs every op through the reused
`_decode_request → extra="forbid" schema → argv builder → shell=False` chain.

### 4. Per-OS op vocabulary (the only new attack surface)

All 13 ADR-0001-era ops are Windows-native (winget/sc/netsh/winreg/schtasks).
"Port all" means a **new** per-OS op vocabulary, defined in
`jarvis/admin/schema_unix.py`. Every new model subclasses `_AdminOpBase`
(`frozen=True`, `extra="forbid"`) so it inherits the exact same strict
validation, and every user-controlled field is **pattern-validated argv only** —
a malicious payload such as `package="git; rm -rf /"` fails the regex before any
argv is built:

- **Linux:** `AptInstallOp`/`AptRemoveOp` (package regex
  `^[a-z0-9][a-z0-9+\-.]{0,127}$`), `SystemctlOp` (unit regex; action
  `start|stop|enable|disable|restart`), `UfwRuleOp`/`UfwRemoveOp` (port
  `1..65535`, action `allow|deny`, proto `tcp|udp`).
- **macOS:** `BrewInstallOp`/`BrewRemoveOp` (formula regex), `LaunchctlOp`
  (reverse-DNS label regex; action `load|unload|enable|disable`).
- **Shared (every OS):** `WriteProtectedPathOp` — reused verbatim; only the
  validated path strings differ (`/etc/…`, `/usr/…`, `/Library/…`,
  `/Applications/…`).

The `AdminOperation` discriminated union, `ADMIN_OPERATION_TYPES`, and
`DESTRUCTIVE_OPS` become a **platform superset**: a single helper decodes any op
by its `type` discriminator, and the executor dispatches each op to its per-OS
argv builder (`["apt-get","install","-y",op.package]`,
`["systemctl",op.action,op.unit]`, `["ufw",op.action,f"{port}/{proto}"]`,
`["brew","install",op.formula]`, `["launchctl",op.action,op.label]`) — argv
only, `shell=False`, exactly the Windows executor's contract.

The destructive new ops (`apt_remove`, `brew_remove`, `ufw_remove`,
`systemctl` (covers stop/disable), `launchctl` (covers unload), and the shared
`write_protected_path`) are registered in `DESTRUCTIVE_OPS` so the per-action
approval gate (`client.py`) fires **identically across OSes** (Mandat §6.2 —
per-action prompt even at autonomy level `trusted`).

## Consequences

+ The security core is provably unchanged — `test_hmac_replay.py` is the
  unmoved-core regression guard; the bytes-level HMAC/nonce/timestamp checks run
  on every OS verbatim. No per-OS re-implementation of the crypto envelope.
+ Two clean seams (`AdminTransport`, `Elevator`) localize all OS-specific code;
  the Windows path is grandfathered untouched (AD-7), so no closed Windows bug
  is re-opened.
+ `UnixSocketTransport`'s `0700`-dir + `0600`-socket + peer-credential check
  gives the same owner-only guarantee the SDDL-ACL gave on Windows, plus the
  HMAC envelope on top (defense in depth).
+ The per-OS op vocabulary keeps the pattern-validated-argv + `extra="forbid"` +
  `shell=False` invariants — the only new attack surface inherits the existing
  hardening.
+ `NullElevator` makes the headless VPS a first-class case: privileged ops
  degrade to a typed, spoken-safe refusal instead of crashing or silently
  succeeding.
- **Not CI-testable end-to-end:** interactive auth (a UAC prompt, a polkit
  dialog, a Touch-ID/password sheet) cannot run on a CI runner. The wave relies
  on AD-3 one-time live sign-off (Wave 4) plus heavy unit tests against a fake
  transport + fake elevator (no `unittest.mock`, EK-3). The schema validation
  and argv-builder layers ARE fully CI-provable (pure Python) — the
  `tests/unit/admin/` suite runs on all three OS legs.
- The discriminated union is now a superset across OSes, so a wrong-OS op (e.g.
  an `apt_install` sent to a Windows helper) decodes successfully but has no
  executor branch on that host; the executor returns a typed
  `unknown_op_type`/unsupported response rather than executing.

## Alternatives Considered

- **Re-implement HMAC/envelope per OS.** Rejected — multiplies the most
  security-critical code by three and breaks the single-regression-guard
  contract. The envelope is transport-agnostic by construction; only the byte
  transport differs.
- **localhost TCP on Unix.** Rejected for the same reason ADR-0001 rejected it
  on Windows — an open port is scannable by any same-user process; `AF_UNIX` +
  filesystem ACL + `SO_PEERCRED` is the Unix-idiomatic owner-only channel.
- **`sudo` as the only Linux elevator.** Rejected as the *default* — polkit
  (`pkexec`) gives a proper authorization dialog and a policy file; `sudo` is
  kept as a fallback, and `NullElevator` covers the headless case.
- **Drop Admin on non-Windows (keep ADR-0001 as-is).** Rejected — violates the
  cloud-first doctrine and the AD-2 "port all six" commitment.

## Cross-References

- Superseded ADR: `docs/adr/0001-ipc-named-pipe-hmac.md` (Windows pipe + HMAC).
- Frozen decisions: `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md`
  (AD-12, AD-3, AD-6, AD-7, EK-6).
- Wave brief: `docs/plans/cross-platform-mac-linux/WELLE-3-admin.md`
  (sub-tasks 3.1–3.6).
- Regression guard (unmoved security core):
  `tests/unit/admin/test_hmac_replay.py`.
- Implementation:
  - `jarvis/admin/transport.py` (`AdminTransport`, `NamedPipeTransport`,
    `make_admin_transport`) — sub-task 3.1.
  - `jarvis/admin/unix_socket.py` (`UnixSocketTransport`) — sub-task 3.2.
  - `jarvis/admin/schema_unix.py` (per-OS op vocabulary) — sub-task 3.3.
  - `jarvis/admin/executor.py` (per-OS argv builders) — sub-task 3.3.
  - `jarvis/admin/elevator.py` (`Elevator`, `UacElevator`, `PolkitElevator`,
    `SudoElevator`, `MacAuthElevator`, `NullElevator`, `make_elevator`) —
    sub-task 3.4.
  - `jarvis/admin/client.py` / `jarvis/admin/helper.py` (seam wiring) —
    sub-task 3.6.
- Fakes: `tests/fakes/fake_admin_transport.py`, `tests/fakes/fake_elevator.py`.
- Safety mandate: `jarvis/admin/schema.py` module docstring (§Safety); CLAUDE.md
  AP-1/AP-2/AP-3 (subprocess hygiene, no-secrets-via-voice, ToolExecutor-only).
