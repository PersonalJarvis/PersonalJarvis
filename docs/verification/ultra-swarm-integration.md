# Ultra Agent Swarm integration evidence

## October 1 main integration

T3 integration: the branch had fallen 503 commits behind main and could not
merge. Main revision `daca63cb4` is now merged. Of 316 conflicted paths, 258
were generated bundle files; those were taken from main and rebuilt. The 58
source conflicts were resolved as follows:

- Exception-comment conflicts in 33 files took main's wording (AP-30 holds on
  both sides).
- Both sides had independently moved select menus to `BrandedSelect`, made
  interface languages lazy, and offloaded blocking chat-control handlers.
  Main's versions were kept. The branch's separate `coreLocales` loader and
  its test setup file were removed.
- Combined resolutions keep both sides: the Society runtime keeps main's
  usable-plugin cache plus Swarm's request bridge and fenced shutdown, with
  conversation storage closed off the event loop. Capability selection keeps
  main's `is_never_granted` spelling check plus the reserved `swarm-` owner
  namespace. The macOS and Linux installer jobs wait for main's release
  admission gate and still honour the manual Windows-only diagnostic.
- Main's new `run-app-action` router tool can reach `/api/swarm` routes. Every
  mutating Swarm route carries `x-jarvis-dangerous`, so the tool asks first. The
  tool is also in Society `NEVER_GRANTED` and the worker forbidden list, so the
  reviewed-plan launch boundary is unchanged.

Fallout from main's newer contracts was fixed. The three Swarm preparation
commands and `swarm-preparation` gained voice aliases in all three locales; the
command reference was regenerated. The watchdog keeps the branch's logged
ownership-marker failures. Tests were brought up to date for main's browser-call
start retraction, its WebServer bootstrap probe and the installer admission
gate. A multipart test now asserts the rejection itself instead of a
library-specific message.

Local evidence on Windows: 707 Swarm-focused Python tests passed (1 skip). The
complete frontend suite passed 5,387 tests across 592 files, and the
production build succeeded. Targeted reruns of every CI-reported new failure
pass. A broader local unit run was stopped by the host's memory-pressure guard
at 27 % and is not counted. Remaining CI-only failures in
`test_agent_login_flow`, `test_codex_auth` and `test_segment_and_history` touch
files identical to main and are treated as unrelated timing failures, not as
fixed.

Native installer qualification on the merged branch, offline with zero
provider requests:

- [Run 36834583000](https://github.com/PersonalJarvis/PersonalJarvis/actions/runs/36834583000)
  on `3608adadd`: Linux AppImage and both macOS DMGs passed installation,
  bundled Wasm, team/lead persistence across same-artifact replacement and
  cleanup. Windows timed out in main's frozen-browser smoke while still in
  the `installing` phase. The smoke now prints each setup step and elapsed time.
- [Windows-only repeat 36838442071](https://github.com/PersonalJarvis/PersonalJarvis/actions/runs/36838442071):
  the browser became ready in 60 s, which matches main's 39–60 s range. The
  earlier stall did not recur and its cause is unknown. Same-artifact
  replacement then failed. A further repeat (36840605379) reported Inno Setup
  exit code 5 (abort on a file it could not replace), with no process running
  from the target directory.
- [Windows-only repeat 36842828236](https://github.com/PersonalJarvis/PersonalJarvis/actions/runs/36842828236)
  on `23e09dfe8` passed everything, including replacement and cleanup. The
  verifier now keeps Inno's own logs (`installer-N.log`) beside its report,
  so a recurrence names the locked file. The two Windows failures are retained
  as an intermittent risk, not relabeled as passes.

A full-suite Windows shard exposed a pre-existing race in main's dictation
history: `os.replace` hit a transient Windows share lock under the store's own
lock. History writes now use the bounded retry the statistics sidecar already
had. The helper moved to `jarvis/dictation/_locks.py` and both stores share it.

Still open, unchanged by this integration: the native one-key live proof (no
`SWARM_INSTALL_TEST_KEY` repository secret exists), the final combined
browser journey, and the ordinary-agent unrestricted host-shell scope decision.

## September 27 native follow-up

T2 follow-up: existing installer lifecycle and HTTP handlers. Windows and Linux
exercise real child-process shutdown; macOS uses the same POSIX implementation
but still requires the rebuilt native campaign. No provider contract changes.

On `7b04675bf`, Linux passed native Swarm installation, replacement, Wasm and
identity checks, then failed the separate browser probe during shutdown. Both
Mac variants passed the Swarm checks but failed workspace removal with
`ENOTEMPTY`. The background browser installer now has an owner cancellation
signal, rejects new work during shutdown, and joins its actual setup thread.
POSIX command groups are reaped synchronously before that thread exits. The
native verifier gives remaining application processes bounded shutdown time
even when their AppImage launcher exits first. Native requalification is open.

The Linux lifecycle/packaging selection passed 67 tests with five platform skips;
Windows focused installer/origin checks passed 17, combined shutdown/Mars checks
passed 29, and the final changed-code selection passed 31. These sets overlap.
Four inherited blocking API handlers now offload their synchronous work. Their
CLI/contract selection passed 407 tests; all 71 route modules remain mounted and
tagged, and regenerating the CLI reference produced no content change.

Boot measurement previously could import another globally registered editable
checkout. The harness now pins its own repository, covered by two child-process
origin tests. The first correctly pinned sample exceeded the unchanged startup
limits (window 7.958 s, interactive 31.661 s, voice usable 32.159 s). A subsequent
sample after local work settled passed at 1.888/15.818/15.845 s respectively.
Both outcomes are retained; this is not a low-variance cold-machine guarantee.
Older samples below did not capture import origin and cannot qualify this head.

The bounded four-platform Grok test was explicitly approved on September 26.
No model key has been uploaded yet, and the native model budget remains unused
while installation failures are diagnosed.

The offline campaign on `06a0214e4` passed Windows installation, replacement,
Wasm and cleanup. Both Mac variants passed those Swarm checks, then failed the
separate browser-provisioning check because frozen OpenSSL lacked public CA
roots. Linux passed fresh/replacement Swarm checks but failed workspace removal;
the original report did not preserve its underlying OS error. The verifier now
records only error type and numeric OS codes, and failed verification retains
built installer artifacts for diagnosis without enabling release publication.

The browser bootstrap now uses verified bundled CA roots unless an explicit
trust override is configured. A source host on Python 3.11 or 3.13 provisions the
locked managed Python 3.12 instead of reusing an incompatible interpreter. Frozen
relative state/wiki paths now follow the configuration profile rather than the
read-only or temporary program bundle; resource imports and absolute paths keep
their existing meaning. These changes passed 730 path/wiki tests with one skip,
114 packaging/browser tests with one skip, and 24 final focused tests. The sets
overlap, and native requalification is still required.

The entry-bundle regression was traced to eagerly importing every core language.
Restored lazy loading preserves the updated translations and reduces the entry
from 1,982 KB to 1,278 KB, below the unchanged 1,350 KB limit. Before this locale
follow-up, all 4,759 UI tests passed; its Linux build lacked a copied test-harness
asset, while the canonical build passed. The subsequent locale run passed 4,761
tests and again exceeded the static guard's timeout. Its candidate filter now
looks for the opening JSX tag rather than common selection-state identifiers;
the structural AST check and five-second limit remain unchanged. The complete
Linux repeat passed all 4,762 tests, and the production build passed with the
authored assets included. An injected multiline native select still failed the
guard, as required. The corresponding GitHub frontend job also passed.

## September 26 integration

The candidate integrates main revision `57e472439`, including the current
Society memory, browser, voice setup and ordinary-agent Mars runtime. The later
Linux bootstrap fix from `7fba5133a` is also integrated; its eight real Bash
bootstrap tests passed in a minimal Linux Python container. Swarm
execution and its goal/question/plan/launch flow remain separate from that world.
This continuation is not final acceptance.

The compact command index now includes all five guided Swarm commands and their
approval bindings while remaining below its existing 3,000-character limit.
The CLI selection passed 346 tests before the main merge.

Shutdown fences new Society work, drains admitted Swarm/chat/HTTP writers, then
closes the shared store. A closed Society runtime cannot reopen: recovery uses
a new owner with the same durable queue. Cancellation-resistant writers keep
their storage open for a retry, while independent browser-release failures do
not strand SQLite after its writers finish. The five-second browser cleanup
deadline remains enforced. The combined Society/Mars/Swarm lifecycle selection
passed 40 tests; current memory/review coverage passed 41 with one platform skip.

The initial broader Python selection passed 2,693 tests, skipped 51 optional or
platform cases, and found five failures. Each failure was fixed and retested.
The final chat, CLI, route, permission and mandatory-guard selection passed
1,387 tests with one skip. The fix preserves the chat's coding-session tool
when its surface also supplies browser tools. It does not change approval scope.
Two lifecycle modules passed the targeted type check; changed Python sources
passed lint. These test selections overlap.

Frontend integration retains rich-text input and collapsed tool details,
current provider navigation, and themed selection controls. The rebuilt
production bundle passed type checking and build. The 13 tests covering the final
UI corrections passed in Linux. A subsequent complete Linux run passed 4,758
tests; its remaining full-tree AST guard exceeded the existing five-second test
timeout at 5.62 seconds. The guard now skips AST construction when the required
literal tag token is absent, retaining the same structural check and timeout.
The complete repeat is still being collected.

The first loaded startup sample passed window and interactive budgets but
missed the 20-second voice budget by 185 ms; it is not counted as a pass.
The repeat after local build/test processes exited passed all unchanged limits:
window 1.896 s, interactive 16.612 s and voice usable 16.856 s (8/20/20 s budgets).

The current browser plugin fails during bootstrap because a packaged service
module is missing. No new browser walkthrough or actual prompt-file selection
is claimed, and no alternate browser mechanism bypasses the earlier denial.
No model credential has been transferred to CI. Native one-key execution and
the unrestricted ordinary-agent host-access interpretation remain open.

The upstream installer CI now permits historical release age while still
verifying signatures. A green historical-artifact job does not establish the
default production wrapper's one-day freshness requirement. No production
wrapper policy is relaxed by this integration.

## September 23 evidence

Date: 2026-09-23. Scope: the Swarm candidate integrated with private agent learning,
automatic effort routing, settings navigation, isolated routine chats and revised
onboarding from main revision `f4dfad6ea`. Individual measurements and native
artifacts remain bound to their recorded revisions. This is an interim
qualification report, not a declaration that every acceptance criterion passes.

## Executed checks

| Check | Result | Boundary |
| --- | --- | --- |
| Integrated Python selection, Windows Python 3.11, four test processes | 2,063 passed; 5 skipped | Swarm unit/integration/contracts, real PostgreSQL/Redis/S3 services, Society, agent chat, lifecycle, packaging and four mandatory guards; no live provider calls |
| Complete frontend Vitest suite before the intake follow-up | 4,425 passed locally; CI later exposed one race | Exact merged frontend source including settings navigation, in an independent dependency directory |
| Production frontend build | Passed | Generated distribution rebuilt from the merged source |
| Isolated startup budget | Passed | Window 1.837 s; interactive 18.469 s; voice usable 19.111 s, against existing 8/20/20 s budgets |
| Learning receipts and shutdown | 49 passed; 1 skipped | Receipt writers drain before storage closes; unfinished reviews persist; reopening does not duplicate subscriptions |
| Snapshot and native cleanup fixes | 85 passed; 1 skipped | Includes actual PostgreSQL backup validation; overlaps the integrated selection |
| Full typed-record parity and related API/storage contracts | 140 passed | All 12 typed browser records, inherited fields, nullable values, SQL/DTO/API roundtrips and exact counters above 2^63; overlaps earlier checks |
| Native descendant observation under four-way launch pressure | 100 completed | Four exited Windows process records briefly remained observable; all returned exit code 1, with the longest observed wait 15 ms |
| Intake follow-up | 70 Swarm UI tests passed; full Linux frontend 4,427 passed; production builds passed | The new committed-frame regression fails before the fix; the complete Linux suite runs from exact revision `9d37206fa` in a clean Node 22 container |
| Native diagnostic follow-up | 94 passed; 1 skipped | Persisted phase receipts, failure status, credential redaction, containment and a bounded verification-step timeout |
| Further integration guards | 231 realtime tests and 7 WebGL guard tests passed | Removed a duplicate write to a read-only test property; the static guard now follows called relative lifecycle hooks and still rejects missing cleanup |
| Routine/onboarding integration | 1,180 Python tests passed; 3 skipped; 13 further setup tests passed; 762 related frontend tests and 55 onboarding tests passed | New main changes integrated; production bundle rebuilt; these selections overlap earlier evidence |
| Two-worker live-proof contracts | 102 passed; 1 skipped | Distinct worker identities, accepted shared input, current-attempt execution/verification receipts and retained artifact hashes |

## Two-worker source calibration

The updated optional live verifier requires two distinct workers: one executes
and saves `numbers.json`; a statistics worker consumes that accepted dependency
and saves `statistics.json`. Both tasks require real execution and deterministic
acceptance, and the verifier checks the shared input in the consumer's evidence.

A real configured Grok/grok-4.3 source run passed in 19.89 seconds, using 10,636
tokens and leaving zero token/cost reservations. The recorded cost was 26,591
microUSD. The accepted result is count 4, sum 40 and mean 10; the complete
[calibration evidence](ultra-swarm-two-worker-calibration.json) includes worker
identities, artifact hashes and usage receipts. This uses the existing local
profile and does not establish fresh native one-key isolation. The unchanged
workflow budget is 60,000 tokens, with a 180-second task runtime bound.

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
has passed actual Linux AppImage and both ARM64/Intel macOS DMG installation,
bundled Wasm, team/lead persistence through same-artifact replacement, and
workspace cleanup. The Windows verification did not finish after more than
30 minutes and the remaining run was stopped for diagnosis. These runs use disposable profiles
and make zero provider requests; they do not include the later settings UI merge.
The canceled Windows job provided neither a completed report nor downloadable
logs at inspection time, so its root cause remains unknown. The verifier now
writes allowlisted phase receipts, emits stack-only diagnostic snapshots, and has
a 35-minute CI step limit. A manual Windows-only option permits focused retries;
tagged releases still require all platform jobs. This improves diagnosis and
does not itself establish a Windows installation pass. The
[Windows-only repeat on `9d37206fa`](https://github.com/PersonalJarvis/PersonalJarvis/actions/runs/35838325214)
then passed real installation, Wasm, retained team/lead identity, same-artifact
replacement and full workspace cleanup, with zero provider requests. The first
run's root cause remains unknown; its cancellation is not relabeled as success.

The full CI frontend run found one intake race, with 4,424 other tests passing:
questions could render before saved answers were restored, allowing the delayed
effect to overwrite an immediate selection. A React Profiler regression reproduces
the inconsistent commit. The component now accepts the view and its answers in
one update, while a refresh of the same revision preserves local edits. The new
regression fails on the previous implementation and passes with the fix; all 70
related Swarm UI tests and the production build pass. A clean Linux Node 22
container subsequently passed the complete 4,427-test frontend suite and build.
The earlier complete local suite is not relabeled as that post-fix result.

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
