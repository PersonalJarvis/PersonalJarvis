# Independent agent server

The independent server owns agent execution. Desktop windows and browsers are
clients of the same HTTP and WebSocket application. Closing a client does not
cancel a turn, release an approval, stop a runtime, or transfer the databases
to another process.

## Start and connect

In Settings, enable **Independent agent server** for the next desktop launch.
Leave its address empty to start a server on this computer. Existing integrated
desktop installations retain their current mode unless this setting is enabled.
The integrated desktop must finish closing before the independent server takes
ownership; a client refuses to evict an existing owner.

The equivalent entry points are:

```console
python -m jarvis --persistent-server
python -m jarvis --connect
python -m jarvis --connect https://jarvis.example.com
```

A local client starts or reuses the server, guarded by a launch lock. Multiple
clients may connect. A remote client uses the server's login page; it never
sends its local control credential, provider keys or subscription login to the
remote server. Remote origins require HTTPS; a local SSH tunnel may use HTTP.
Credentials must not be embedded in the address.

The server remains available when no work is scheduled. It stops only when
explicitly stopped, the operating system exits, or the process fails. The tray's
stop action and the authenticated `POST /api/agent-server/stop` stop the server;
closing a client does neither. `GET /api/agent-server/status` distinguishes
server ownership, completed agent startup and a startup failure. Both routes
are available through the dynamic `jarvis api agent-server` command group.

For login startup, the existing background-only option includes the persistent
server flag when a local independent server is selected. On a headless host,
use the operating system's process supervisor for restart after process or host
failure. This is a user process on Windows, not a SYSTEM service.

## Ownership and recovery

The server owns the roster, chat events, routine scheduler, conversations,
memory, skills, delegation, provider access, model gateway and tool authorization.
Jarvis, Hermes and OpenClaw use those same services. External runtimes live on
the server and reach its local model and MCP endpoints; a remote UI does not
need either runtime installed.

Agent services start after the HTTP surface is available, even if no client
opens the Society page. Startup itself makes no model warm-up request.
Runtime preparation and provider calls retain their existing account and
background billing policies.

The existing chat stream replays stored events after a sequence cursor. Live
approvals remain owned by the server when a client disconnects. Reconnection
includes pending approvals and current turn state. A missing client never grants
permission. Completed effects and terminal events remain stored across server
restarts. A server crash is different from a client disconnect: unfinished turns
use the existing interruption recovery and are not blindly replayed. Check their
stored result before explicitly continuing.

## Devices and deployment boundaries

The independent client provides the web application and browser voice. Local
wake word, overlays, native capture and desktop automation still require an
available device and its permissions. The integrated desktop remains available
for those features. A remote server cannot access the client's screen merely
because the client opened a page. Server-side file and shell operations address
the server; connected-computer operations keep their explicit target.

The SSH placement of a coding CLI is separate from connecting a UI to a Jarvis
server. The server connection does not expand the supported SSH runtime list
or grant new device access.

## Verification

The disconnect contract is exercised for Jarvis, Hermes and OpenClaw dispatches
with scripted runners: disconnect during approval, reconnect with a cursor,
approve once, collect one result, and reopen the durable store. The runtime tests
exercise ACP subprocess translation and session restoration against scripted
agents. These checks do not claim live model-provider or remote-host execution.

Server ownership tests cover concurrent startup boundaries, refusal to take over
a persistent owner, explicit stopping, atomic configuration and endpoint validation.
The local process proof must additionally start the real backend, close and
reopen clients, and verify that the same server remains healthy.
