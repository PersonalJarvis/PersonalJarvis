# Mars runtime reference

Status: opt-in development reference, not the completed colony. The previous
world remains available. Open **Agents > Mars preview** to inspect the new
foundation; the Ledger and communications-station controls remain separate
from rendering. Characters, rover travel and remaining districts are unfinished.
The art study has no user visual approval and must not be rolled out by family.

## Boundaries

The packaged `jarvis/society/mars/definition.json` defines metre/Y-up placement,
stable district/building/station IDs and navigation version. The frontend copy
is synchronized by `python scripts/sync_mars_definition.py --check` and a
contract test. No old-world terrain or figure art is the new visual foundation.
Existing agent IDs, custom imports, recipes and old-world preferences remain
unchanged. The preview selector changes only its URL parameter.

The communications console accepts a bounded draft request through the existing
Society scheduler and the chosen agent's read-only chat runner. It does not send
mail or publish content. Credential-shaped input is rejected before persistence.
Request identity, station ownership, fencing and task/result references are
durable; failed or uncertain outcomes never become animation-driven success.
The renderer is not an execution dependency. Unknown outcomes are reconciled
without blindly repeating dispatch or cancellation.

The `mars` OpenAPI group provides definition, snapshot, bounded cursor events,
draft submission and scoped cancellation. Dynamic CLI discovery respects the
selected server and isolates cached schemas by its full base URL. Example:

```text
jarvis --url http://127.0.0.1:47869 api mars --help
jarvis --url http://127.0.0.1:47869 --json api mars get-mars-snapshot
```

## Window ownership

Background mode is explicit, default-off and session-only. On supported Windows
hosts it requires persistent WebView storage and confirmed native tray presence.
Closing the real client window then retains a blank hidden GUI keeper and the
owning backend. Reopening restores a new authenticated window; disabling the
mode restores the ordinary close-to-quit policy. Explicit Quit still ends work.
Browser-dependent voice is not retained by the keeper.

Other desktop backends currently report this mode unavailable until native tray
registration can be proved. Their ordinary windows and headless server remain
available. No Windows SYSTEM service or server-side graphics renderer is added.
Local work cannot continue when its execution host is stopped or asleep.

Windows native closure/reopening and a real draft continuing after window
destruction have been measured. Direct tray-menu interaction, other native OSes,
host sleep/reboot, a fresh single-key install and final graphics/performance
acceptance are still open; the private issue tracker retains the full ledger.

## Restart recovery

An existing `mars/ordinary.db` under the configured data directory schedules
recovery after the server boot chain yields. No browser tab, desktop renderer or
HTTP request is needed to start that recovery. An installation without this
journal does not construct the Society runtime or create Mars storage for it.
The initial HTTP request and deferred recovery share one application-owned
initialization task with a thirty-second deadline, service and journal owner.
An individual HTTP disconnect cannot cancel initialization for other waiters.
Explicit server stop fences late initialization, cancels and joins the owned
startup/reconciliation tasks, then closes the journal before Society shutdown.
A collaborator that ignores cancellation produces an explicit shutdown timeout
after five seconds; it cannot publish a late station owner. The server retains
the Mars stop latch and task references, completes independent browser, chat,
plugin, watcher, terminal and server teardown, then reports the incomplete Mars
cleanup. A Mars timeout must not leave those unrelated resources running.

Acknowledged queued requests are checked against current agent authority before
dispatch. Previously owned work receives a new fence and an interruption event;
the existing executor inspects its stable command/task identity rather than
dispatching it again. Completed result references remain unchanged. An outcome
the executor cannot establish stays uncertain and holds the station for
reconciliation; this does not promise exactly-once external effects.

The portable lifecycle contract exercises separate abrupt-exit and recovery
processes at queued, active and result-committed checkpoints, using real SQLite
and a clearly labeled test executor. It also covers concurrent first access,
authority revocation, startup/shutdown races and private error logging. This is
local process-failure evidence; actual host power loss, sleep/reboot, unavailable
provider recovery and live external-outcome reconciliation remain separate
acceptance work. No OS-specific lifecycle API or new execution scheduler is
introduced by this recovery path.

## Art and verification

Editable Blender source, recipes and isolated exports live in
`art/studies/mars-outpost-reference`. Runtime users need only the packaged GLB;
they do not install Blender or obtain private reference images. Original concept
inputs remain private. The source recipe validates packed portable paths,
geometry, UVs, normals and linear vertex colors; numeric tests do not approve
appearance. See the study source README for rebuild commands and limitations.

Focused checks live in `tests/contract/test_mars_*.py`, the Mars unit tests,
`tests/unit/test_outpost_reference.py`, and frontend `components/society/mars`.
Keep functional, visual and operational acceptance separate. A successful
compile or technical export is not approval of the finished Outpost.
