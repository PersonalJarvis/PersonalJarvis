# Managed Local Realtime — Supervisor Assessment

**Date:** 2026-08-11

**Status:** Assessment of the shipped implementation; five fixes landed, the
rest is a ranked backlog.

**Scope:** The managed local realtime provider Jarvis owns and runs — crash
behaviour, time-to-ready, and portability to machines the maintainer cannot
test. Companion to [`local-realtime-runtime-deep-dive.md`](local-realtime-runtime-deep-dive.md)
(2026-08-08), which diagnosed the architecture. This one audits what the code
does *now*.

## What changed since the deep dive

Most of that document's P0 list shipped. Verified in the current tree:

| P0 item | State | Evidence |
| --- | --- | --- |
| Loopback bind and client | Done | `derive_launch_command`, `_force_loopback_bind` migrates legacy commands at the spawn boundary |
| Cross-process instance lease | Done | `_exclusive_spawn_guard` (msvcrt/fcntl), held across install, spawn, stop, uninstall |
| Sweep + verify all descendants | Done | `_stop_owned_unlocked` always sweeps `_kill_by_install_root` after the tree kill; Windows result checked via `_pid_exists` (`WaitForSingleObject`, not `OpenProcess`) |
| Protocol readiness instead of TCP | Done | `probe_runtime` validates a full `/v1/pool` payload; TCP stays diagnostic and is never a kill criterion |
| Stop calling unsupported `/v1/models` | Done | `_resolve_model` short-circuits for managed commands |
| Bounded Ollama voice context | Done | `prepare_voice_brain_command` creates a `…-voice-8k` alias at 8192 tokens |
| Real audio round-trip in the smoke | Done | `_smoke_boot` runs `probe_voice_roundtrip_sync` through the shipped adapter |
| **Rotate logs, persist restart state** | **Was open** | Fixed in this pass — see below |
| Treat bind failure as fatal to the tree | **Still open** | See C3 |

Two capabilities were added that the deep dive asked for and that this
assessment builds on: a continuous supervisor (`start_runtime_monitor` /
`_runtime_monitor`) and boot forensics (`boot_progress.py`).

## How the managed server behaves today

### Starting — four entry points, one spawn path

1. `prespawn_transport` fires at app boot, spawn-only, before the voice gate.
2. `warm_transport` runs behind the gate: `ensure_running` → `wait_until_ready`
   → `repair_smoke_marker_from_live_runtime` → `start_runtime_monitor` →
   `warm_brain`.
3. `can_open_duplex_session` handles a call: it asks the pool directly, and on
   a cold server starts one in the background but waits only
   `_LOCAL_MANAGED_PREFLIGHT_WAIT_S` (0.75 s) before refusing with a live stage
   and ETA.
4. The REST route `/managed-server/start` plus an off-request warm task.

All four funnel into `ensure_running` under `lifecycle_guard` — a non-blocking
thread lock wrapping a kernel-held file lock. That is what makes their race
harmless, and it is the strongest part of the design.

`ensure_running` refuses rather than queues, naming the reason:
`not-local`, `spawn-in-progress`, `install-running`, `port-in-use`,
`rate-limited`, `stuck-process`, `managed-process-survived`,
`ownership-failed`, `brain-context-profile`, `generation-changed`,
`unverified-owner`.

### Staying alive

`_runtime_monitor` is a daemon thread polling ownership every second and the
pool every five. It distinguishes two illnesses:

- `unready` — the process lives but `/v1/pool` stopped answering;
- `unavailable` — every slot is draining/stuck **and** no client is active.

Both need 30 seconds of continuous bad state, and an `unavailable` replacement
additionally re-verifies the exact generation (`_owned_generation`: pid +
create-time + spawn token) under the lifecycle lease before killing anything.
This is careful work: a live call can never be terminated by the watchdog.

The monitor arms only on a proven install (`install.server_status()["ready"]`)
and re-checks that before every autonomous revive — fail-closed by design.

### Recovering and reporting

`_cleanup_timed_out_generation` re-probes readiness at the timeout boundary and
compares the generation under the lease, so a server that became ready one
millisecond late is never killed. `status()` reports `reachable` (TCP), `ready`
(pool), `available` (free slot), `owned`, and `stale` as separate verdicts —
the conflation the deep dive complained about is gone.

`boot_progress.parse_boot_stage` names the live phase from module-prefixed log
markers (VAD → STT → LLM → TTS → api), and `crash_tail` strips the `/v1/pool`
polling noise that made a real crash's log tail useless.

## Ranked causes — crashes and unavailability

**C1. An unreaped POSIX child read as a live server.** *(fixed in this pass)*
The server is spawned detached and its `Popen` handle is deliberately dropped,
so on Linux/macOS a crash leaves a zombie whose `/proc` entry keeps answering
`create_time()` and `os.kill(pid, 0)`. `_owned_process` therefore reported a
crashed server as healthy, the monitor never saw its alive→gone transition, and
the crash was never recovered — until some unrelated `subprocess.Popen`
elsewhere in the interpreter happened to reap it. Windows has no zombie state,
so this was invisible on the reference machine. This was the single largest
cross-OS reliability gap.

**C2. No escalation after repeatedly failed boots.** *(fixed in this pass)*
`boot_progress` counted a `failed_streak` that only the UI read. A generation
that never reached a ready pool was retried on the same fixed 60-second floor
forever, and each attempt loads gigabytes of weights onto the accelerator.

**C3. A process that lives but never binds stays "starting" for the full
budget.** *(open)* The deep dive's live incident — Uvicorn fails to bind, only
the server thread exits, handler threads keep the models resident — still has
no fast detector. `ensure_running` answers `already-running` while
`age < OWNED_STARTUP_TIMEOUT_S`, so callers are told "starting" for minutes.
The monitor does not help here because it only arms on an already-ready server,
which a never-ready generation by definition is not. The readiness waiter's
`cleanup_on_timeout` is the only reclaim path, and only when someone is waiting.

**C4. Windows console-script launcher death loses ownership mid-boot.**
*(partially open)* `reconcile_ready_ownership` adopts the real listener by
executable identity plus listening port — but it requires a **ready** pool, so
it cannot heal ownership *during* the boot. In that window `_owned_process`
reports not-alive, and `_log_crash_tail` can fire a false "exited
unexpectedly". `warm_transport` heals it afterwards, but only because it calls
`start_runtime_monitor` after readiness; note it attempts the smoke-marker
repair *before* the adoption, so that repair fails on the first attempt.

**C5. One pipeline slot is one global failure domain.** *(open, upstream)*
`--num_pipelines 1` is hardcoded. Any reconnect overlap or slow reclaim rejects
the next caller. Raising it duplicates model instances rather than sharing
them, so this is genuinely blocked on upstream's coordinator work.

**C6. Unbounded server log.** *(fixed in this pass)* Append-only, one uvicorn
access line per readiness poll, no rotation. On the reference machine it stood
at 3.9 MB and grows for as long as the server is healthy.

## Ranked causes — time to ready

**L1. Models load serially before the port exists.** *(structural)* Measured on
the reference machine from the shipped statistics: boots of **43.1, 75.5 and
75.6 seconds**. A healthy cold process is indistinguishable from a dead one for
that entire window. Nothing in Jarvis can fix this inside the pinned server;
only prewarming moves it off the call path, which `prespawn_transport` now does
at the earliest possible moment.

**L2. Brain residency expires after two hours.** *(fixed in this pass)*
`BRAIN_KEEP_ALIVE = "2h"` is a deadline, not a subscription. It slides on every
warm call, but an overnight gap expires it, so the first sentence of the
morning paid a cold Ollama load on an otherwise warm server.

**L3. A hung boot cost five minutes everywhere.** *(fixed in this pass)*
`RUNTIME_READY_TIMEOUT_S` was a flat 300 s on every host, including one whose
own median boot is 75 s.

**L4. Early-crash recovery waits out the full rate limit.** *(open, by design)*
A server dying five seconds after spawn cannot be retried for 55 more seconds,
so the user pays roughly two minutes. The 60-second floor is deliberate
crash-loop protection; a short first retry followed by widening would recover
faster *and* protect better, but it interacts with C2 and should be measured
before changing.

**L5. `install.server_status()` runs in the revive hot loop.** *(open)* It
SHA-256s the whole patched `service.py`, parses all of `jarvis.toml`, and on
POSIX globs the venv lib directory — on every `_revive_from_monitor` attempt,
which during a `refused:rate-limited` stretch means once per second.

**L6. `parse_boot_stage` re-reads 128 KB per status poll.** *(open, minor)*
Roughly 1300 lines × 7 regexes each time the provider card polls.

**L7. The interactive preflight waits only 0.75 s.** *(open, by design)* Correct
— holding a call on "Connecting" for 90 seconds is worse. But the consequence
is that without a successful prewarm the first call is *always* a refusal, and
the user must dial again. Prewarm reliability is therefore the real latency
feature.

## Cross-OS and cross-hardware risks

None of these can be exercised on the maintainer's Windows/NVIDIA box; each is
argued from the code path.

**P1. Zombie semantics (Linux/macOS).** Fixed — see C1. This is the archetype
of the class: correct on Windows, silently broken elsewhere.

**P2. `create_time` drift across suspend/resume (Linux).** Ownership matches
the recorded start stamp within one second. psutil derives that stamp from
`/proc/stat`'s `btime` plus jiffies, and `btime` can shift after a suspend or
an NTP step. A laptop that slept between two Jarvis runs may therefore fail the
match, leaving a healthy server running that Jarvis can no longer own, monitor,
or stop deliberately. Windows and macOS use absolute stamps and are unaffected.
A second identity proof (executable path, already computed by
`_process_identity_is_managed`) would make the check robust without loosening
the PID-reuse guarantee.

**P3. macOS memory budgeting is optimistic.** `_BRAIN_HEADROOM_GB = 6.0` is
checked against *total* usable memory. On Apple Silicon that figure is unified
memory shared with the OS, the browser, and everything else — not free VRAM. A
16 GB Mac passes the 12 GB floor and then co-hosts STT, Qwen3-TTS and the brain
in memory the system is already using. `derive_launch_command` additionally
pins `--qwen3_tts_dtype bfloat16` on `mps`, whose bfloat16 coverage has
historically been uneven.

**P4. Accelerator selection is a binary.** `tts_device = "cuda" if
memory_source == "nvidia-smi" else "mps"`. Today preflight blocks everything
else honestly, so it cannot misfire — but the moment a third accelerator is
detected, a Linux host would be handed `mps`.

**P5. Boot-stage parsing depends on local time.** `_line_epoch` uses
`time.mktime(time.strptime(...))`. A DST transition (ambiguous or non-existent
hour) shifts the comparison by an hour, and a server configured to log UTC
would break the filter permanently outside UTC. The failure is cosmetic — the
card falls back to its static sentence — but the fix is strictly better:
record the log's byte offset at spawn and read only past it. That is exact,
timezone-free, and cheaper than parsing timestamps.

**P6. `_site_packages()` glob ordering.** `sorted(glob("python*/site-packages"))[0]`
sorts lexicographically, where `python3.10` precedes `python3.9`. Harmless for
a freshly created venv with one entry; wrong for a venv carried across a Python
upgrade.

**P7. Test isolation.** Three provider tests read the *real* pidfile, boot
statistics and server log because they never set `JARVIS_DATA_DIR`. On CI and
on a machine with no managed server they pass; on a machine actually running
one they assert against its live boot stage and parse its multi-megabyte log —
which also inverted a timing assertion. Fixed for those three; there is no
project-wide fixture pinning `JARVIS_DATA_DIR` the way `tests/conftest.py`
already pins the Agentic-IDE history, so the class can recur.

## Landed in this pass

All five are driven by measurements the code already collected but never acted
on. Covered by eight new tests in `tests/unit/realtime/local_server/test_supervisor.py`.

1. **`_process_create_time` treats a zombie as dead** (C1) — one guard on the
   single path every ownership question already goes through. An unreadable
   status never revokes ownership, so hardened hosts keep working.
2. **`ready_timeout_s()`** (L3) — three times the recorded median, floored at
   120 s, capped at the previous 300 s. No history keeps the full ceiling. On
   the reference machine's numbers this is 226 s instead of 300 s.
3. **`_spawn_min_interval_s()`** (C2) — spacing widens 60 → 900 s with the
   never-ready streak. One successful boot clears the streak, so an ordinary
   crash after a healthy hour still recovers in the plain window. An explicit
   Start passes `honor_failure_backoff=False`: a human pressing the button
   knows something the statistics cannot.
4. **Monitor re-arms brain residency every 45 minutes** (L2).
5. **`_rotate_server_log_if_large()`** (C6) — 8 MB, rotated at the spawn
   boundary, the one moment no process holds the file open on Windows.

Note that 2 and 3 must ship together: a shorter readiness budget alone would
make a failing install cycle *faster*.

## Ranked backlog

1. **Detect a never-binding generation early (C3).** Once `2 × expected_boot_s`
   has passed with no pool, the process is not booting — it is wedged. The
   statistic to judge that already exists and `_boot_status` already uses the
   same threshold to stop showing a countdown. Arming the monitor on a
   *booting* server (not only a ready one) would also give C4 its healing path.
2. **Cache `install.server_status()` in the revive loop (L5).** An mtime-keyed
   memo over the four component checks; the SHA-256 is the expensive part.
3. **Anchor boot-stage parsing to a byte offset (P5).** Removes the timezone
   and DST dependency and is cheaper than the current parse.
4. **Add the executable-identity proof to ownership (P2).** Survives a Linux
   `btime` shift without weakening PID-reuse safety.
5. **Budget macOS memory against *free* unified memory (P3).**
6. **Project-wide `JARVIS_DATA_DIR` fixture (P7).** The pattern already exists
   in `tests/conftest.py` for Agentic-IDE history.
7. **Reconsider the flat 60 s first retry (L4)** once 1–3 are in and the
   statistics can show whether early crashes are common.

Items 1–3 are the ones that move the two stated pain points; 4–6 are
portability insurance the maintainer cannot buy by testing.
