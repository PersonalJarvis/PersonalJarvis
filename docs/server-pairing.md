# Connect an independent Jarvis server

In **Settings → Computers → Add computer**, choose **Server address** to
connect another running Jarvis instance. Choose **SSH** for remote shell,
terminal, agent installation, and computer execution features.

On the target instance, open the same Server address form and expand
**Allow another computer to connect to this Jarvis**. Generate a pairing code.
The code grants access to that instance's Jarvis interface, expires after five
minutes, and can be used once. Enter the target's HTTPS address and code on
the connecting computer, or paste the pairing URL. HTTP is accepted only for
direct loopback connections, such as an SSH tunnel. Generating a code does not
open network ports, change the bind address, or configure a reverse proxy.
Both instances must support the `jarvis-pairing-v1` protocol.

A successful exchange adds the server to the **Jarvis servers** list. **Open
server** obtains a one-minute, single-use link and opens the server's normal
authenticated interface in the browser. Checks verify the saved credential
without invoking a model. The last check's result is displayed explicitly;
there is no background polling or paid health probe.

Server credentials use the existing OS keyring and headless credential-file
fallback. Connection metadata contains no codes or credentials. The target
persists credential hashes and offers client revocation in the pairing panel.
Disconnecting from the client revokes its remote credential before deleting
the local record; an unreachable target reports failure and keeps that record.
Revocation blocks future connections and pending links. Browser sessions that
were already opened remain signed in until their normal session lifetime ends.

Pairing is an independent-server UI connection, not an SSH transport. Paired
servers are therefore not offered as SSH execution or IDE offload targets.
Use SSH for those workflows, or run work from the paired server's own UI.

The mounted `computer-pairing` API group exposes code creation, redemption,
server listing/checking/opening, and client revocation through the dynamic
Jarvis CLI. Supply secrets through stdin or request-body files, never command
arguments. Code, credential, and ticket responses use `Cache-Control: no-store`.

Validation includes two-sided ASGI HTTP tests through the application's
Host/Origin/credential boundary, persistent-store reload, one-use and expiry
checks, blocked redirects/plaintext remote origins, and frontend interactions.
A Windows loopback HTTP probe with the application's credential backend also
verified pairing, persisted reload, browser session exchange, replay rejection,
remote revocation, and removal of the test credential.
These tests do not constitute proof of an arbitrary external deployment's
TLS, firewall, or network configuration.
