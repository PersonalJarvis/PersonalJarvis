# Swarm cancellation and in-flight exposure measurement

Measured 19 September 2026 on Windows / Python 3.11 with the real Grok 4.3 streaming adapter, production Swarm scheduler, SQLite accounting, ToolExecutor and bundled Wasmtime/QuickJS sandbox. Candidate `9e99a8cce` incorporates upstream `443f61d53`; exact measured source hashes are in the linked JSON evidence.

## Owner stop

Three valid observations stopped a live streamed response after at least one successful JavaScript execution through ToolExecutor. The owner timer starts immediately before `service.control(team_id, 'stop')`; it ends when the observer sees zero team-owned worker tasks, active provider streams and active tool executions. This includes control I/O and observer latency, rather than estimating a remote server timestamp.

| Run | Requested worker tasks | Active calls at stop, including coordination | Control returned (ms) | Owned execution quiescent (ms) | Pending tokens retained | Pending cost estimate retained |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 1 | 2 | 23.97 | 69.47 | 21,483 | $0.053708 |
| 3 | 2 | 3 | 26.13 | 62.78 | 32,278 | $0.080695 |
| 4 | 3 | 4 | 15.97 | 70.50 | 42,326 | $0.105816 |

All three teams reached `canceled` with reason `User requested stop`. No new provider requests, tool invocations or reservations appeared in the two-second observation window. Every interrupted request lacked complete final usage and retained its entire reservation; it was never refunded as zero-cost work. The durable rows remain `reserved` with null actual usage, which represents pending exposure rather than a settled invoice. Runtime shutdown took 1.92–3.43 ms and left zero owned worker tasks, streams or tool executions.

## Exposure and limits

The highest aggregate reservation reconstructed from durable creation/closure timestamps was **42,326 tokens / $0.105816**. The largest individual request reserved **11,559 tokens / $0.028898**. This includes coordination requests, not only worker output. Every observed run requested a 250,000-token budget, 180-second runtime and 2,048 output tokens per model call; its optional monetary ceiling was unset. These are explicit measurement settings and do not modify product defaults.

Production admission reserves serialized UTF-8 prompt/tool/message bytes plus 4,096 framing tokens, 128 per message and the configured output-token maximum before invoking the provider. Money uses the selected model's configured conservative rate. Final complete reported input/output/cache usage can settle a reservation; reasoning follows the provider's usage semantics. Incomplete or ambiguous canceled usage remains fully pending. See `jarvis/swarm/worker.py`, `jarvis/swarm/store.py` and `jarvis/brain/swarm_factory.py` hashes in the JSON.

The values above are conservative local reservation estimates for these concrete requests, not a provider-enforced billing guarantee. No finite universal remote-cost ceiling was established: hidden reasoning, tokenizer differences, provider accounting behavior and post-disconnect billing are not independently observable here. The local 250,000-token admission ceiling is enforced on used plus reserved usage; provider-reported overruns are separately detected by production accounting.

## Budget exhaustion is a distinct behavior

A fifth live run explicitly configured a 12,000-token ceiling. The first request reserved 8,792 tokens ($0.021980); the next reservation was rejected before a second request launched. The team became `blocked` with `Token budget exhausted`. The already reserved stream finished naturally after **19.456 seconds** from the rejection observation and settled to 1,739 tokens with zero pending reserve. No further requests launched during the two-second final observation. Budget exhaustion therefore blocks new admission but is not equivalent to the fast owner-stop cancellation measured above.

## Method and limitations

The helpers only delegate and timestamp real provider/executor/storage operations. They supply no synthetic model response, usage, tool result or artificial transport delay. Each run used a new isolated data directory and the existing credential resolver; no global provider settings or browser profile were changed. The runtime's normal explicit expert task API was used to keep the measurement bounded; this does not test the separate clarification UI flow.

Three small stop samples do not establish a p95, a worst-case guarantee or distributed cancellation latency. HTTP/UI round-trip time before service entry is outside this measurement. Local async generators and clients ending does not prove that remote compute stopped or that billing stopped. No provider invoice was fetched. Only Grok was measured live; other adapter families require their own live measurements before equivalent claims.

The integrated Python qualification passed 1,427 tests, including the synthetic cancellation, unknown-usage, cache/reasoning, retry and provider-family contracts. One Windows symlink-privilege case was skipped. Those synthetic contracts do not replace the live measurements above.

The initial live calibration failed because this helper requested an unsupported records page size (1,000 instead of 200). It is retained as `run-1.json` and excluded from latency statistics; its stopped-team accounting appears in the linked JSON evidence. A budget-helper import typo failed before team creation or model calls; both its original and corrected logs are retained. Neither failure is hidden or classified as a production defect.

The [machine-readable measurements](ultra-swarm-cancellation.json) include per-call timestamps, accounting snapshots, source hashes and the failed calibration. Raw diagnostic logs and isolated runtime profiles are not part of this published evidence.
