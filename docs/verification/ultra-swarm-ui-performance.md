# Swarm browser projection performance

Measured 19 September 2026 with the shipped application and real local HTTP/WebSocket routes. **PASS** for the stated sample and limits; raw measurements and source hashes are in [the JSON evidence](ultra-swarm-ui-performance.json).

## Reference and workload

AMD Ryzen 7 7800X3D, 8 physical cores / 16 logical processors, 33,995,051,008 bytes RAM, Windows 11 build 26200, Chrome 153. The flat team view was used, without WebGL. Other desktop processes remained running.

The large fixture contains 1,000 explicitly synthetic idle worker records plus the normal lead record, split into 50 aggregate nodes, and 20 ready fixture tasks. A separate small fixture contains 99 synthetic workers plus the lead and renders all 100 individual nodes. Both teams remain Created with zero inference; these are rendering/storage fixtures, not a thousand-agent model run. A controller appends clearly labeled synthetic projection events through the real store. The producer targets 50 events/s; the final fixture recorded 48,051 events over 1589.782 seconds. Its full application process peak RSS was 430.2 MiB, including ordinary application services, not an isolated Swarm increment.

## Rendered frame intervals

Each foreground sample lasts approximately 12 seconds. Percentiles use nearest rank over requestAnimationFrame intervals. No hidden-tab sample is accepted. These are delivered frame intervals, not separate GPU execution timings. The target is p95 at most 33.3 ms.

| View | Agent/group nodes | Intervals | p50 ms | p95 ms | Maximum ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| dark-aggregate | 50 | 1729 | 6.90 | 7.00 | 7.30 |
| light-aggregate | 50 | 1726 | 6.90 | 7.00 | 20.90 |
| light-group-drilldown | 21 | 1728 | 6.90 | 7.10 | 7.20 |
| light-100-individuals | 100 | 1729 | 6.90 | 7.00 | 7.10 |
| final-paced-aggregate | 50 | 1729 | 6.90 | 7.10 | 7.30 |

Dark and light grouped views, group drill-down and the 100-node individual view were observed in the real browser. The final grouped sample followed a page reload with the corrected pacing route. Reload reconstructed the same durable fixture and group counts.

## Transport, including the initial snapshot

The normal sample lasts 20 seconds, the slow-consumer sample waits 1.2 seconds between reads for 12 seconds, and the reconnect sample lasts 10 seconds. Each begins with the real HTTP snapshot and then opens the cursor-bearing WebSocket. Counts include JSON envelopes and the initial HTTP snapshot; message rate is measured in every half-open one-second delivery window. Payload target is 65,536 bytes and rate target is two updates/s. A slow consumer is tested at the stated delay, not every possible network condition.

| Phase | Messages incl. HTTP | Maximum bytes | Minimum interval s | Peak messages/s |
| --- | ---: | ---: | ---: | ---: |
| normal | 34 | 20153 | 0.500 | 2 |
| slow_consumer | 11 | 20152 | 0.625 | 2 |
| reconnect | 20 | 20177 | 0.500 | 2 |

Every frame retained the selected team identity, at most 50 aggregate nodes and at most 100 individual records. Reconnect delivered a new durable snapshot rather than replaying the prior client's buffered frames. The separate API regression sends client messages between revisions and proves they cannot accelerate outbound updates.

## Retained failure and correction

Before the fix, HTTP snapshot plus immediate WebSocket frames arrived at 0, 0.078 and 0.578 seconds: three updates in the first second. This failed observation remains in the JSON. The route now uses a monotonic outbound deadline, including a cursor handshake delay; receiving a client message cannot shorten that deadline. The first four rendered-frame samples preceded this transport fix. The final grouped sample and all transport phases above used it.

No model inference, remote provider capacity, optional 3D rendering, low-end hardware performance or scheduled background object expiry is inferred from this experiment. The UI samples and transport campaign use the same durable-record presentation and normal scoped routes as the product.
