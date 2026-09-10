# Connected computers

This development milestone adds paired computers, remote tools, API-agent hosting,
workspace handoff and independent agent copies. It does not yet complete the whole
distributed-computing roadmap: moving the hub itself, host-local CLI seats, native
connector installers/autostart, and the remaining live platform acceptance are open.

## How it works

One Jarvis instance is the hub. It owns the roster, canonical conversations,
memories, schedules and permissions. A connector opens an outbound connection to
that hub. An agent's execution host and the computer its task operates on are
separate choices. A workstation running only the UI may close while a reachable
hub and execution host continue working.

Open **Connected computers** from the Agents section or an agent card. The panel
contains pairing, SSH setup, access profiles, task assignment, agent locations,
copying, transfer history and remote job receipts. Existing access settings load
before editing. Output and screenshots are fetched on demand rather than included
in every status poll.

### Connector

Install matching builds on the hub and target. A minimal target uses the base
package; native desktop operation additionally needs the relevant desktop extras.
The connector entry point is:

```text
python -m jarvis.machines --hub wss://hub.example:443 --pair
```

Create a pairing code in the hub's panel, then enter it in the connector's hidden
prompt. Codes expire after ten minutes and can be consumed once. Device credentials
are stored through the existing secret store. The hub stores only their hashes.
These credentials cannot authenticate ordinary management endpoints.

Desktop access is disabled on the connector by default. Explicitly enable `--desktop
own`, `--desktop attached`, or `--desktop both`. Attached operates the visible user
desktop. Own requests an isolated session from the existing screen providers and
never falls back to the visible desktop. OS permission and display requirements
still apply. The connector is a user process, not a Windows SYSTEM service.

Remote connections require WSS with ordinary certificate validation. Cleartext WS
is accepted only for loopback development. Configure a reachable HTTPS endpoint and
the existing trusted-host/proxy settings on the hub; the panel does not provision
a domain, TLS certificate, firewall rule or cloud server.

### SSH

SSH/SFTP uses the optional `remote` extra, included in `full` and `dev`. Setup reads
the server's public fingerprint for explicit confirmation before saving a private
key through the credential store. Authentication checks that pinned key; implicit
SSH config and agent forwarding are disabled. SSH supplies shell and file tools,
not agent hosting or desktops. A broken SSH connection does not prove that a
server-side program stopped, so uncertain outcomes require reconciliation.

AsyncSSH 2.24 requires a newer cryptography build than the project's Windows ARM64
wheel supports. That cell uses 2.23.1; other supported cells use 2.24 or newer within
the compatible major. See the [upstream release notes](https://asyncssh.readthedocs.io/en/latest/changes.html).

## Permissions and execution

Grant access separately for each agent and device. Profiles choose a workspace or
the connected account, shell/files, and a desktop mode. Existing agent grants,
denies, approval rules and `ToolExecutor` remain in force. Workspace-only shell
execution is refused when actual OS isolation is unavailable; setting `cwd` is not
a sandbox. The current connector and plain SSH do not provide that shell sandbox.

`remote-machine` discovers permitted devices and performs shell, file and desktop
operations. **Run task here** carries an explicit target in the persisted scheduler
assignment. The target-bound API surface removes hub-local file/desktop tools and
rejects another machine id. CLI seats which cannot enforce this boundary are
refused rather than allowed to run the task locally.

Hosted API agents maintain their loop on the connector. Model access stays at the
hub, with the selected provider credential; only model-issued tool calls can reach
the hub's existing executor. Text, usage and tool receipts enter the existing chat.
Hosted turns currently provide the society's own tools and remote-machine access;
full plugin/CLI equivalence remains a follow-up requirement.

Connector leases expire after thirty seconds without renewal. Disconnect stops new
work and cancels active connector tasks. It does not undo effects already performed
or independently started services. Jobs are recorded before dispatch, and the
connector journals job ids. Reconnecting reports saved outcomes without replaying
commands. Identical unresolved operations are blocked until a result arrives or the
operator records a verified outcome. Stop requests are not proof of rollback.

Desktop input requires a fresh observation token and matching foreground identity.
Screenshots use the existing image-artifact path back to the model. A visible
desktop has one agent owner at a time. Desktop stop disables its grants and pauses
the connector's desktop channel until explicitly enabled again.

## Move and copy

Moving queues a durable transfer and blocks new turns for that agent. Existing work
drains first. Files are copied in bounded binary chunks to a new staging directory,
with SHA-256 verification. The hub then changes host ownership under a generation
check. The source remains intact on failure. Restart recovers unfinished transfers
without activating their destination. Symbolic links, external Git-worktree pointers
and recognizable credential files are refused. This is a filesystem handoff, not a
live migration of processes, browser sessions or installed software.

Copies get a new identity, independent knowledge and skills, selected files, an empty
chat and no subscription login. Copied routines and the new agent start paused.
Assign the copy's device access before moving or activating it. A partial copy stays
paused; an existing agent name is never overwritten.

CLI entry points include `jarvis machines list`, `jarvis machines hosts`,
`jarvis machines move <agent-id> --host <device-id> --yes`, and
`jarvis machines transfer <transfer-id>`. All management routes are also exposed
through `jarvis api machines`.

## Verification and remaining acceptance

Contract coverage includes single-use enrollment, revocation, device-bound results,
duplicate suppression, lease expiry, path guards, binary round trips, clone identity,
model-issued call authorization, desktop ownership, changed focus, and real loopback
SSH host-key validation. Browser checks use the actual panel and management API with
a real local connector socket; both themes and a workspace handoff were inspected.
Windows screen acquisition, capture and teardown were also exercised without
injecting input into the user's desktop.

The original acceptance still requires the user's Linux VPS, real macOS and Linux
desktop input, production connector packaging/autostart, full hosted tool parity,
and a coordinated hub handoff. These are not implied by passing emulated OS tests.
