# Ultra Agent Swarm integration evidence

Date: 2026-09-23. Scope: the Swarm candidate integrated with private agent learning,
automatic effort routing and settings navigation from main revision `bf899713b`.
The settings follow-up changes frontend sources only; backend evidence remains
bound to production revision `2cdb52dee`. This is an interim
qualification report, not a declaration that every acceptance criterion passes.

## Executed checks

| Check | Result | Boundary |
| --- | --- | --- |
| Integrated Python selection, Windows Python 3.11, four test processes | 2,063 passed; 5 skipped | Swarm unit/integration/contracts, real PostgreSQL/Redis/S3 services, Society, agent chat, lifecycle, packaging and four mandatory guards; no live provider calls |
| Complete frontend Vitest suite | 4,425 passed; no failures | Exact merged frontend source including settings navigation, in an independent dependency directory |
| Production frontend build | Passed | Generated distribution rebuilt from the merged source |
| Isolated startup budget | Passed | Window 1.837 s; interactive 18.469 s; voice usable 19.111 s, against existing 8/20/20 s budgets |
| Learning receipts and shutdown | 49 passed; 1 skipped | Receipt writers drain before storage closes; unfinished reviews persist; reopening does not duplicate subscriptions |
| Snapshot and native cleanup fixes | 85 passed; 1 skipped | Includes actual PostgreSQL backup validation; overlaps the integrated selection |
| Full typed-record parity and related API/storage contracts | 140 passed | All 12 typed browser records, inherited fields, nullable values, SQL/DTO/API roundtrips and exact counters above 2^63; overlaps earlier checks |
| Native descendant observation under four-way launch pressure | 100 completed | Four exited Windows process records briefly remained observable; all returned exit code 1, with the longest observed wait 15 ms |

The process assertion now verifies process identity and waits for a bounded exit
receipt. A live descendant still fails the assertion, and a reused PID is never
terminated. Snapshot publication retries transient permission failures for less
than one second, preserves atomic visibility and fails on persistent locks or a
competing destination. Both failure and successful retry logs are retained in
the local qualification artifacts. Test sets overlap and must not be summed.

The integrated Python run also emitted existing dependency deprecation warnings
and a Windows Proactor closed-pipe finalizer warning. The test process exited
normally. These warnings are not reported as silent clean-runtime evidence.

## Runtime and remaining qualification

The [native campaign on `2cdb52dee`](https://github.com/PersonalJarvis/PersonalJarvis/actions/runs/35832789369)
has passed actual Linux AppImage and Intel macOS DMG installation, bundled Wasm,
team/lead persistence through same-artifact replacement, and workspace cleanup.
Windows and ARM macOS results remain pending. These runs use disposable profiles
and make zero provider requests; they do not include the later settings UI merge.

The existing public source-installer smoke test separately rejected release
`v2.2.1` because its Rekor inclusion proof exceeded the wrapper's one-day freshness
bound. Both signature axes passed before that rejection. This existing public-
release gate is not bypassed, and no release or re-signing is part of this change.
The first portable-dependency matrix attempt ended with a `uv` process crash
after resolution; the repeated matrix passed without changing dependency pins.

The isolated preview returned HTTP 200 and shut down with process exit code 0.
The browser extension timed out on network and DOM inspection, so this round does
not establish a new browser journey. Earlier goal/questions/saved-plan/explicit-
launch/result evidence remains historical. Real automated file selection also
remains unverified. The preview reported a separate optional browser-sidecar
dependency incompatibility on Python 3.11 and a main-WebSocket double-close
diagnostic during shutdown; neither is counted as a successful clean UI pass.

Fresh native Windows/Linux/macOS qualification on the merged revision, live
single-key native execution, the final combined multi-team user journey and the
outstanding ordinary-agent unrestricted host-shell scope decision remain open.
No model credential was uploaded to CI. The native live-provider mode is opt-in
and uses a bounded task; its offline contracts and local source calibration do
not substitute for native one-key execution.

Existing measurements are published separately:

- [Local dispatch and queue latency](ultra-swarm-local-dispatch.md)
- [Cancellation and in-flight budget exposure](ultra-swarm-cancellation.md)
- [Actual storage-service faults](ultra-swarm-storage-faults.md)
- [Rendered UI and event-stream measurements](ultra-swarm-ui-performance.md)
