# Local dispatch with 32 simultaneously active inference calls

Run: 20260919T134721761330Z. Overall result: PASS.

The actual held topology is **31 worker calls plus one persistent lead supervisory call**, not 32 workers. The lead is created after the unchanged five-second supervision cadence. Its queue latency starts at its durable task readiness; Start-to-32-active time is reported separately.

All 32 worker tasks and every normally created supervisory task are included in the full queue distribution. The final worker waits for a worker slot while the first 31 calls are held; its multi-second latency is retained, including in the maximum. No task is removed from the percentile calculation.

| Trial | All-task n | Queue p50 (s) | Queue p95 (s) | Queue max (s) | Peak calls | Start to 32 active (s) | Duration (s) | Peak RSS (MiB) | Result |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 0 | 34 | 0.346 | 0.538 | 10.089 | 32 | 5.205 | 12.733 | 70.2 | PASS |
| 1 | 34 | 0.417 | 0.742 | 9.299 | 32 | 5.211 | 12.699 | 71.7 | PASS |
| 2 | 33 | 0.359 | 0.800 | 9.636 | 32 | 5.072 | 11.824 | 74.9 | PASS |

The [raw JSON evidence](ultra-swarm-local-dispatch.json) retains hardware, exact source hashes before/after, real durable task/agent/attempt snapshots at the 32-call barrier, every first-inference timestamp, every call lifetime, exact limits, token reconciliation, percentile definition, per-trial CPU time and resource samples. Source hashes must remain unchanged for this run to qualify.

Three fresh service/team/storage trials use one Python process, with no explicit warm-up or discarded sample. Synthetic inference and tool output use no network or real model; production service, scheduling, authorization, SQLite storage, budget reservations, tool routing and result verification execute normally. This is a local dispatch benchmark on a shared desktop, not a 32-worker or real-model throughput claim.

Reference hardware: AMD Ryzen 7 7800X3D, 8 physical cores / 16 logical processors, 33,995,051,008 bytes physical RAM, Windows 11 Pro build 26200. Python version and all measured source hashes are retained in the JSON. Process CPU time per trial was 8.844 / 7.938 / 7.000 seconds; persisted trial storage was 919,117 / 927,309 / 906,829 bytes. This was an ordinary shared desktop run; unrelated processes were not stopped.

The exact peak cohort of 31 workers plus one lead has p95 queue delays of 0.519 / 0.679 / 0.675 seconds. The distribution over all 32 worker tasks alone also passes at 0.538 / 0.742 / 0.800 seconds, so the additional lead records do not manufacture the passing result.

An independent readback (`swarm-local32-proof-check.py`) derives the peak from every inference call's entry/exit intervals, joins durable attempts to actual agent roles, recomputes p95 from raw timestamps, checks every worker result succeeded and zero reservations remain. It passes all three trials. The 32 calls overlap for 0.264 / 0.269 / 0.268 seconds after independent storage inspection. Readback output and the exact proof JSON hash are saved in the [independent readback](ultra-swarm-local-dispatch-check.json).

Retained unsuccessful helper run: `swarm-local32-full-runs/20260919T134622983134Z/proof.json`. That run reached 32 inference calls but requested an unsupported `attempts` owner-record collection while inspecting the barrier; all three trials were marked failed. The corrected helper uses a read-only storage transaction to inspect attempts. No production source, ceiling, clock, task, principal, or scheduler was mutated to correct that observation failure. The previous September 12 benchmark and both new run logs remain intact.
