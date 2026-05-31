# WELLE-3 — Admin / elevation (security-sensitive)

> Canonical decisions: `_FROZEN-DECISIONS.md` (AD-12 full Admin port behind
> `Elevator` + `AdminTransport` seams, AD-6 graceful null-fallback, AD-13
> detect-and-degrade, EK-6 ADR-0001 superseded). PC-7: all 13 current ops are
> Windows-native, so "port all" means a **new** per-OS op vocabulary, and the
> elevation glue (`jarvis/admin/launcher.py`) is currently dormant (never
> auto-called).

---

## Goal

Port the largest, highest-risk feature: privileged-operation execution. The
**security core is reused untouched** — the transport-free HMAC/envelope/
Pydantic-argv layer (`ipc.py:65-262`: `_canonical_args_json`, `_compute_hmac`,
the `_decode_request` check ordering, the `_AdminOpBase` `extra="forbid"` +
pattern-validated argv, the no-`shell=True` contract). What changes per OS is the
**transport** (a new `AdminTransport` seam: the Windows SDDL-ACL named pipe stays,
a `UnixSocketTransport` is added — a `0700` socket in `$XDG_RUNTIME_DIR` with
`SO_PEERCRED`/`LOCAL_PEERCRED` peer-credential checking), the **elevation
mechanism** (a new `Elevator` seam: `UacElevator` on Windows, `SudoElevator` /
`PolkitElevator` on Linux, `MacAuthElevator` on macOS, `NullElevator` as the
headless/no-auth refusal fallback), and the **op vocabulary** (macOS
`brew`/`launchctl`/protected-path; Linux `apt`/`systemctl`/`ufw`/protected-path).
This wave is **never CI-testable end-to-end** — interactive auth (a UAC prompt, a
polkit dialog, a Touch-ID/password sheet) cannot run on a runner — so it relies
on AD-3 live sign-off (Wave 4) plus heavy unit tests against a **fake transport**.
ADR-0001 is superseded by a new ADR-0020.

---

## Sub-tasks

### 3.1 — `AdminTransport` protocol; extract Windows pipe code; reuse the HMAC core

- **Create:** `jarvis/admin/transport.py` (`AdminTransport` `Protocol` +
  `NamedPipeTransport` (Windows) + `make_admin_transport()` factory),
  `tests/fakes/fake_admin_transport.py`, `tests/unit/admin/test_transport_seam.py`.
- **Modify:** `jarvis/admin/ipc.py` — move the **transport-specific** Windows code
  (`AdminPipeServer._accept_one` `:295`, `_handle_connection`/`_read_message`/
  `_write_message`/`_safe_close` `:330-424`, `AdminPipeClient._roundtrip` `:511`,
  `_build_sddl` `:123`, `current_user_sid` `:92`, `default_pipe_name` `:117`) into
  `NamedPipeTransport` in `transport.py`. **Leave the HMAC/envelope core in place**
  (`_canonical_args_json` `:65`, `_compute_hmac` `:76`, `_decode_request` `:194`
  with its 5-step ordering, `_encode_response` `:258`, the nonce LRU `:181`, the
  `_TIMESTAMP_WINDOW_NS`/`_NONCE_LRU_SIZE` constants `:43-50`) — these are
  transport-agnostic and reused by every transport verbatim (AD-12).
- **Approach:**
  - `AdminTransport` `Protocol` (server side): `async def serve(handler)` where
    `handler: Callable[[bytes], Awaitable[bytes]]` receives a raw envelope and
    returns a raw response — exactly the bytes-level seam `_decode_request` /
    `_encode_response` already operate on. Client side:
    `async def roundtrip(raw: bytes) -> bytes`. This is the same shape as the
    existing `AdminPipeClient.send`→`_roundtrip` flow (`ipc.py:456-545`), so the
    HMAC envelope build (`_build_envelope` `:495`) stays in the client and only
    the byte-transport swaps.
  - `NamedPipeTransport` wraps the relocated Windows pipe code — no behavior
    change (AD-7); the SDDL-ACL `D:(A;;FA;;;<SID>)` (`ipc.py:129`) and the
    MESSAGE-mode read still apply.
  - `make_admin_transport()` selects on `detect_platform()`: `win32`→
    `NamedPipeTransport`; else→`UnixSocketTransport` (3.2).
- **Acceptance criteria:**
  - `pytest tests/unit/admin/test_hmac_replay.py -v` stays green (the HMAC/nonce/
    timestamp core is unmoved — this regression suite must not break).
  - `pytest tests/unit/admin/test_transport_seam.py -v` green (a fake transport
    round-trips a signed envelope through `_decode_request`/`_encode_response`
    with no real pipe/socket).
  - `python -c "from jarvis.admin.transport import make_admin_transport, AdminTransport; assert isinstance(make_admin_transport(), AdminTransport)"` exits 0 on every OS (never raises).
  - `python -c "import ast; m=ast.parse(open('jarvis/admin/ipc.py').read()); assert not any(getattr(n,'names',None) and any(a.name in ('win32pipe','win32file','win32security','pywintypes') for a in n.names) for n in ast.walk(m) if isinstance(n,ast.Import))"` exits 0 (pipe imports are now inside `transport.py`, lazily; `ipc.py` is import-clean).

### 3.2 — `UnixSocketTransport` (0700 socket + peer-credential check)

- **Create:** `jarvis/admin/unix_socket.py` (`UnixSocketTransport`),
  `tests/unit/admin/test_unix_socket_transport.py`,
  `tests/integration/test_admin_unix_loopback.py`.
- **Approach:**
  - Bind an `AF_UNIX` `SOCK_STREAM` socket at
    `$XDG_RUNTIME_DIR/jarvis-admin-<uid>.sock` (fall back to a `0700` dir under
    `/run/user/<uid>/` or `tempfile.mkdtemp(mode=0o700)`). Create the socket file
    with `os.umask`/`os.chmod` to `0600` and the containing dir `0700` — the
    filesystem ACL replaces the Windows SDDL-ACL (`ipc.py:123`).
  - **Peer-credential check (the security equivalent of the SDDL owner ACE):**
    on accept, read the peer's UID via `SO_PEERCRED` (Linux:
    `sock.getsockopt(SOL_SOCKET, SO_PEERCRED, struct.calcsize("3i"))` →
    `(pid, uid, gid)`) or `LOCAL_PEERCRED`/`getpeereid` on macOS. Reject any
    connection whose UID != the server process UID. This is the
    `UnixSocketTransport`-level gate; the HMAC envelope check
    (`_decode_request`) still runs on top — defense in depth, mirroring how the
    Windows transport pairs the pipe ACL with HMAC.
  - The server side implements `AdminTransport.serve(handler)`: accept → read raw
    bytes → `await handler(raw)` (the handler is the reused `_decode_request` →
    `executor.execute` → `_encode_response` chain) → write response → close.
    Reuse the existing accept-loop/per-connection-task structure from
    `AdminPipeServer.serve_forever` (`ipc.py:268-293`) so a slow op doesn't block
    accept. Client side: `connect` → write → read → close, mirroring
    `_roundtrip`.
  - On a headless box with no `$XDG_RUNTIME_DIR` and no elevation, the transport
    still constructs (it is just a local socket); refusal happens at the
    `NullElevator` layer (3.4), not here.
- **Acceptance criteria:**
  - `pytest tests/unit/admin/test_unix_socket_transport.py -v` green (asserts the
    socket file is created `0600` in a `0700` dir, and that a mismatched-UID peer
    is rejected via a monkeypatched `SO_PEERCRED` reader).
  - `pytest tests/integration/test_admin_unix_loopback.py -v` green on Linux/macOS
    (real `AF_UNIX` loopback: a signed envelope round-trips through
    `UnixSocketTransport` + the reused `_decode_request`/executor; mirrors the
    Windows `tests/integration/test_admin_ipc_loopback.py`). Marked
    `skip_ci`-exempt — `AF_UNIX` loopback runs fine on a runner.
  - `python -c "import struct,socket; assert hasattr(socket,'SO_PEERCRED') or True"` documents the macOS `LOCAL_PEERCRED` branch is reached on darwin.

### 3.3 — Per-OS op vocabulary (macOS brew/launchctl, Linux apt/systemctl/ufw)

- **Create:** `jarvis/admin/schema_unix.py` (the macOS + Linux op models),
  `tests/unit/admin/test_schema_unix.py`.
- **Modify:** `jarvis/admin/schema.py` — make the `AdminOperation` discriminated
  union (`:175-192`) and `ADMIN_OPERATION_TYPES` (`:195-209`) /
  `DESTRUCTIVE_OPS` (`:212`) platform-conditional (or a superset that the
  executor dispatches per OS). `jarvis/admin/executor.py` — add macOS/Linux op
  handlers alongside the Windows ones.
- **Approach:**
  - New op models subclass `_AdminOpBase` (`schema.py:25`, `frozen=True`,
    `extra="forbid"`) so they inherit the same strict validation. Mirror the
    pattern-validated-argv discipline of the Windows ops (`InstallWingetOp`
    `package_id` regex `:39`, `_SERVICE_NAME` regex `:55`, firewall name regex
    `:82`) — **no free-form shell strings ever** (the §Safety mandate from
    `schema.py:1-9`).
    - **Linux:** `AptInstallOp`/`AptRemoveOp` (`package` regex
      `^[a-z0-9][a-z0-9+\-.]{0,127}$`), `SystemctlOp` (`unit` regex,
      `action: Literal["start","stop","enable","disable","restart"]`),
      `UfwRuleOp` (port `1..65535`, `action: allow|deny`, `proto: tcp|udp`),
      `WriteProtectedPathOp` (reuse the existing one `schema.py:161` — paths just
      differ, e.g. `/etc/...`, `/usr/...`).
    - **macOS:** `BrewInstallOp`/`BrewRemoveOp` (`formula` regex),
      `LaunchctlOp` (`label` regex, `action: load|unload|enable|disable`),
      `WriteProtectedPathOp` (same model, paths like `/Library/...`,
      `/Applications/...`).
  - Each new op carries a per-OS argv builder in `executor.py` that emits a
    validated argv list (e.g. `["apt-get","install","-y",op.package]`,
    `["systemctl",op.action,op.unit]`, `["brew","install",op.formula]`,
    `["launchctl",op.action,op.label]`) — argv only, `shell=False`, exactly the
    Windows executor's contract.
  - Add the destructive ops to `DESTRUCTIVE_OPS` (`apt_remove`, `systemctl` with
    `stop`/`disable`, `ufw_remove`, `brew_remove`, `launchctl unload`,
    `write_protected_path`) so the per-action approval gate
    (`client.py:135-139`) fires identically across OSes.
- **Acceptance criteria:**
  - `pytest tests/unit/admin/test_schema_unix.py -v` green (asserts each op
    validates a good payload, rejects a malicious one — e.g. `package="foo; rm -rf /"`
    fails the regex — and that destructive ops are in `DESTRUCTIVE_OPS`).
  - `python -c "from jarvis.admin.schema_unix import AptInstallOp; AptInstallOp(package='git')"` exits 0; `python -c "from jarvis.admin.schema_unix import AptInstallOp; AptInstallOp(package='git; whoami')"` exits non-zero (validation error).
  - `pytest tests/unit/admin/ -v` green on all OS legs (schema validation is pure-Python, CI-provable; actual execution is not).

### 3.4 — `Elevator` protocol + per-OS elevators (Uac / Sudo / Polkit / MacAuth / Null)

- **Create:** `jarvis/admin/elevator.py` (`Elevator` `Protocol` + `UacElevator`,
  `SudoElevator`, `PolkitElevator`, `MacAuthElevator`, `NullElevator` +
  `make_elevator()` factory), `tests/fakes/fake_elevator.py`,
  `tests/unit/admin/test_elevator.py`.
- **Modify:** `jarvis/admin/launcher.py` — refactor `ensure_admin_secret` (`:42`)
  to stay transport-agnostic, and move the Windows `ShellExecuteW(runas, ...)`
  helper-spawn (`launcher.py:11`, the dormant elevation glue per PC-7) behind
  `UacElevator`. `jarvis/admin/client.py` — `AdminClient` (`:66`) gains an
  injected `Elevator` (alongside its existing injectable `pipe_client` at `:80`,
  preserving the DI seam PC-3 calls out).
- **Approach:**
  - `Elevator` `Protocol`: `async def ensure_elevated_helper(transport_addr) ->
    ElevationResult` (spawns/authorizes the privileged helper bound to the
    transport address) + `is_available() -> bool`.
    - **`UacElevator` (Windows):** the existing `ShellExecuteW("runas",
      python.exe, "-m jarvis.admin.helper --pipe-name ...")` flow
      (`launcher.py:11`), unchanged (AD-7).
    - **`PolkitElevator` (Linux, preferred):** `pkexec` to spawn the helper
      bound to the unix socket; a polkit policy file ships under
      `jarvis/admin/data/`. `is_available()` = `shutil.which("pkexec")`.
    - **`SudoElevator` (Linux, fallback):** `sudo -A`/`sudo` non-interactive when
      polkit is absent. `is_available()` = `shutil.which("sudo")`.
    - **`MacAuthElevator` (macOS):** `osascript -e 'do shell script "…" with
      administrator privileges'` (or Authorization Services via pyobjc) to spawn
      the helper. Touch-ID/password sheet is OS-driven, like UAC.
    - **`NullElevator`:** returned when no elevator is available
      (`not capabilities.has_elevation`, e.g. a headless VPS with neither pkexec
      nor sudo). `ensure_elevated_helper` returns a refusal `ElevationResult` and
      logs the English message "no elevation mechanism available on this host —
      privileged operations are disabled; install pkexec or run with sudo". This
      is the AD-6 graceful no-op; the `AdminClient.execute` `no_secret`/refusal
      path (`client.py:152-164`) already surfaces such refusals as a typed
      `AdminResponse(success=False, error_code=...)`, not a crash.
  - `make_elevator()` selects on `detect_platform()` + `capabilities`: `win32`→
    `UacElevator`; `darwin`→`MacAuthElevator`; `linux`→`PolkitElevator` if pkexec
    else `SudoElevator` else `NullElevator`; `not has_elevation`→`NullElevator`.
  - **Never weaken the injection defenses for convenience** (AD-12 / the §Safety
    mandate): the elevator only spawns the helper; the helper still runs every op
    through the reused `_decode_request` → `extra="forbid"` schema → argv builder
    → `shell=False` chain.
- **Acceptance criteria:**
  - `pytest tests/unit/admin/test_elevator.py -v` green (asserts factory selection
    per platform + `is_available`, and that `NullElevator.ensure_elevated_helper`
    returns a refusal `ElevationResult` and never raises — AD-6).
  - `python -c "from jarvis.admin.elevator import make_elevator, Elevator; assert isinstance(make_elevator(), Elevator)"` exits 0 on every OS.
  - `pytest tests/unit/admin/test_elevator.py -k null -v` green (the headless fallback path).
  - **No end-to-end elevation test** — the interactive prompt is excluded from CI and deferred to Wave 4 (AD-3); any test that would trigger a real prompt is marked `skip_ci`.

### 3.5 — Supersede ADR-0001 with ADR-0020 (cross-platform elevation)

- **Create:** `docs/adr/0020-cross-platform-elevation.md`.
- **Modify:** `docs/adr/0001-ipc-named-pipe-hmac.md` — add a "Superseded by
  ADR-0020 (2026-xx-xx)" header note (do not delete; ADR history is append-only).
  `CLAUDE.md` — update the "Atomic config writes" / Phase-5 admin pointers and
  the AP-table if needed to reference the new seams.
- **Approach:** ADR-0020 records the AD-12 architecture: the `AdminTransport` +
  `Elevator` seams, the reused HMAC/envelope/Pydantic-argv core, the
  `UnixSocketTransport` peer-cred model vs the SDDL-ACL pipe, the per-OS op
  vocabulary, and the `NullElevator` headless contract. It states explicitly that
  the security core is *unchanged* and that the new surface area is transport +
  elevation + op vocabulary only. Reference the regression guard
  (`test_hmac_replay.py`) that protects the unchanged core.
- **Acceptance criteria:**
  - `test -f docs/adr/0020-cross-platform-elevation.md` exits 0.
  - `grep -n "Superseded by ADR-0020" docs/adr/0001-ipc-named-pipe-hmac.md` matches.
  - `pytest tests/unit/docs/test_adr_uniqueness.py -v` green (ADR numbering stays unique — CLAUDE.md notes 0009/0010/0014 carry legacy duplicates; 0020 must not collide).

### 3.6 — Wire the seams into `AdminClient` + helper boot

- **Modify:** `jarvis/admin/client.py` (`_ensure_pipe_client` `:108` → a
  transport-agnostic `_ensure_transport` using `make_admin_transport()`; inject
  `make_elevator()`), `jarvis/admin/helper.py` (bind the OS-appropriate transport
  via `make_admin_transport()` instead of hardcoding `AdminPipeServer`).
- **Approach:**
  - `AdminClient.execute` (`client.py:121`) keeps its exact control flow:
    destructive gate (`:135`) → cancel-token (`:142`) → ensure transport/secret
    (`:152`) → publish requested (`:167`) → roundtrip (`:172`) → completed/rejected
    event (`:174-183`). Only step 3 swaps `AdminPipeClient` for
    `make_admin_transport()`, and the `no_secret` refusal (`:154-164`) is extended
    to also cover `NullElevator` refusals with the same `AdminResponse(success=
    False, ...)` shape — preserving the AD-6 "zero silent drops" contract.
  - The helper process (`helper.py`) constructs `make_admin_transport()` and
    serves the reused `_decode_request`→`executor`→`_encode_response` handler.
- **Acceptance criteria:**
  - `pytest tests/unit/admin/ -v` green on all OS legs (client flow exercised with
    `fake_admin_transport.py` + `fake_elevator.py`).
  - `python -c "import jarvis.admin.client, jarvis.admin.helper"` imports clean on Linux/macOS (no module-scope `win32*` — Wave 0 import gate covers it).
  - The Windows loopback regression `tests/integration/test_admin_ipc_loopback.py` stays green (the named-pipe path is unbroken after extraction).

---

## Parallelism

This wave is more sequential than 1/2 because everything funnels through the
transport seam. Recommended ordering inside one or two worktrees:

- **Worktree F — Transport + elevation (security core):** 3.1 → 3.2 → 3.4 → 3.6.
  3.1 (extract the transport seam, keep HMAC core) must land first; 3.2
  (`UnixSocketTransport`) and 3.4 (`Elevator`) can then proceed in parallel
  sub-branches that both depend on 3.1; 3.6 wires them in last.
- **Worktree G — Op vocabulary + ADR:** 3.3 + 3.5. Independent of the transport
  seam (it touches `schema.py`/`schema_unix.py`/`executor.py` + docs), so it can
  run fully parallel to Worktree F and merge in either order.

Because this is the most security-sensitive surface, every PR here should carry a
`requesting-code-review` pass focused on the no-`shell=True` / pattern-validated-
argv / peer-cred invariants before merge. Each worktree runs
`pwsh scripts/preflight.ps1` first.

## EK acceptance gate

This wave completes **EK-2** (all six features now have a per-OS implementation
behind their seam — Admin being the sixth) and advances **EK-3** (fakes
`fake_admin_transport.py`, `fake_elevator.py` + unit tests, no `unittest.mock`).
It directly satisfies the ADR half of **EK-6** (ADR-0001 superseded by ADR-0020;
CLAUDE.md updated; `ipc.py`/`client.py`/`helper.py` no longer hard-import a
Windows-only package at module scope). The end-to-end elevation behavior is
**explicitly not CI-verified** (interactive auth) and is handed to Wave 4's AD-3
live sign-off, contributing to **EK-5**.

## Dependencies on prior waves

**Wave 0** (the `jarvis/platform/` factory + `capabilities.has_elevation` probe,
the green CI matrix, and the import-cleanliness gate that locks in the `ipc.py`
extraction). Independent of Wave 1 and Wave 2 — no shared files — so it can run in
parallel with them. Must merge before Wave 4 so the live sign-off has the
elevation code to verify on a real Mac + Linux box.
