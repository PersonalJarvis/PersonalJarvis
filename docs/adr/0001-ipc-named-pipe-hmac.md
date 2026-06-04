---
title: "ADR-0001: IPC via Named Pipe + HMAC"
slug: adr-0001-ipc-named-pipe-hmac
diataxis: adr
status: active
owner: sam
last_reviewed: 2026-04-29
phase: 5
audience: developer
---

# ADR-0001 — IPC between the Jarvis app and the Admin Helper: Named Pipe + HMAC

> **Superseded by ADR-0020 (2026-05-29).** The cross-platform port
> (`docs/adr/0020-cross-platform-elevation.md`, AD-12) generalizes this design:
> the **security core is reused unchanged** (HMAC-SHA256 + nonce-replay LRU +
> timestamp window + the `extra="forbid"` pattern-validated-argv + `shell=False`
> contract; regression guard `tests/unit/admin/test_hmac_replay.py`), but the
> **transport** moves behind an `AdminTransport` seam (the Windows SDDL-ACL named
> pipe described below becomes `NamedPipeTransport`; a `UnixSocketTransport` with
> a `0700` socket + `SO_PEERCRED`/`LOCAL_PEERCRED` peer-credential check is added
> for macOS/Linux) and **elevation** moves behind an `Elevator` seam
> (`UacElevator`/`PolkitElevator`/`SudoElevator`/`MacAuthElevator`/`NullElevator`).
> This ADR is retained as the canonical record of the original Windows design;
> the Windows pipe path is grandfathered untouched (AD-7).

**Status:** Superseded by ADR-0020 (originally Accepted 2026-04-22)
**Phase:** 5 — Admin Capability

## Context

The Admin Helper (`jarvis_admin_helper.py`) runs as a separate, UAC-elevated process, while the main app stays `asInvoker`. The main app has to send the helper whitelisted operations (winget install, service start/stop, firewall rule, registry write, scheduled task, …) and stream the results back. The IPC choice determines attack surface, latency, and portability.

## Decision

**Windows Named Pipe + HMAC-SHA256 + Nonce.**

Pipe name: `\\.\pipe\jarvis-admin-<user-sid>` (unique per user, confusion impossible).
Security descriptor via SDDL: `D:(A;;FA;;;<current-user-SID>)` (full access only for the starting user, Everyone/Authenticated Users excluded — the default ACL for named pipes is too permissive, see mandate §150).

Payload format (per request):
```json
{"nonce": "<16-byte-hex>", "timestamp_ns": 1745..., "op": "install_winget",
 "args": {"package": "7zip.7zip"}, "hmac": "<sha256(nonce||ts||op||args, shared_secret)>"}
```

Shared secret: 32 bytes random, generated on the first helper start, stored in the Windows Credential Manager under the key `jarvis_admin_hmac` (setup-wizard entry). The helper reads it once at startup and keeps it in memory.

Nonce replay protection: the helper keeps an LRU cache of the last 256 nonces and discards requests with `|now - timestamp_ns| > 30s`.

## Consequences

+ No ports, no firewall rule, no TCP scanner can see it.
+ The SDDL ACL excludes other users on the same machine (relevant for shared-use scenarios).
+ HMAC + nonce + timestamp window provide three independent attack-protection layers.
+ Native on Windows, no extra deps — `pywin32.win32pipe` + `win32security` are enough.
- Not portable to Linux/Mac. **Not relevant:** Jarvis is Windows-first (CLAUDE.md §Windows specifics).
- Named-pipe handling with async is fiddlier than HTTP. The wrapper classes `AdminPipeClient`/`AdminPipeServer` in `jarvis/admin/ipc.py` hide that.

## Alternatives Considered

- **localhost TCP (e.g. port 47822):** Open port, fundamentally scannable by other processes of the same user. ACL only via SO_EXCLUSIVEADDRUSE (not reliable on Windows). Rejected.
- **gRPC/Protobuf:** Overkill for a point-to-point connection with a fixed vocabulary. +Dependency (`grpcio`, ~30 MB). Rejected.
- **COM interface (IDispatch):** Would be very Windows-idiomatic, but COM marshalling between integrity levels has historically been an attack bonanza (CVE-2019-1405 etc.). Rejected.
- **Shared memory + semaphore:** No request/response semantics, additionally needs a signal channel. Rejected.

## Open Implementation Questions

- Async wrapper: `asyncio.StreamReader`/`StreamWriter` via `asyncio.open_connection`? Or thread-based with `win32pipe` + `loop.run_in_executor`? → Decision to be made while writing in 5.1-B, not ADR-worthy.
- Pipe-disconnect detection: both sides must have keep-alive, otherwise zombie connections remain. → `WriteFile` with a 100ms timeout, reopen the pipe on failure.
