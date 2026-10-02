# Browser profiles and shared logins

Open **Browser profiles** in Society or an agent's browser panel. A saved
profile can be assigned to all current and future agents, selected agents, or
one agent. Individual agents can inherit the shared default, use their own
existing profile, or use a named profile. Creating a profile alone does not
change any assignments.

## Choose a browser

- **Separate agent profile** creates a durable managed browser identity. Sign
  in through manual control in the visible browser. Assigned agents reuse it.
- **Connect Chrome profile** connects the real Chrome profile in which the
  extension is installed. Website sessions stay in Chrome. Enter permitted
  website domains explicitly, for example `x.com`.

The Chrome connector is an initial implementation. Download its ZIP from the
profile dialog, unpack it, enable Developer mode at `chrome://extensions`, and
choose **Load unpacked** in the intended Chrome profile. Open the extension
popup and enter the local server address and five-minute pairing code displayed
by Jarvis. Refresh the profile list to verify the connection. Chrome Web Store
distribution is not included; the UI does not claim one-click installation.

Each extension installation receives a distinct local pairing identity. Google
account email, extension ID and tab ID are not used as profile identity. Pairing
state persists locally, is not synchronized through Chrome Sync, and can be
revoked from Jarvis. Reinstalling the extension requires pairing again.

## Sharing and account changes

**All agents** updates current assignments and sets the default for future
agents. An individual **Own profile** override can subsequently opt out.
**Selected agents** grants exactly the selected existing agents access to that
profile. Existing agents losing an assignment wait for an explicit new choice;
they do not silently resume an old account. Changing an assignment stops the
affected browser turn and disconnects its old viewer.

A profile has one active owner. Another agent must wait until the current
task, manual control and open viewer have released it. A profile lock also
prevents another Jarvis instance from opening the same profile concurrently.
Distinct tabs in one Chrome profile are not separate account containers; use
different profiles for different website accounts.

Removing a profile revokes Jarvis access. It does not log the person out of
their personal Chrome or delete browser cookies. Removed assignments remain
unavailable until the user chooses a replacement.

## Google sign-in rejection

Google may reject a browser controlled by automation. A Google rejection page
in the managed browser is not evidence that the user's password is wrong.
On Windows, choose **Sign in** in the managed browser panel. Jarvis closes the
automation connection and opens installed regular Google Chrome with the same
owned profile inside the existing preview. Mouse and keyboard input come only
from the person controlling that panel. No extension installation is needed for
this mode. Complete the login in that window, then choose **Hand back to agent**.

A fresh browser opened by the person starts directly in regular Chrome when
installed Chrome and native capture are available. Agent-initiated browsers
retain automation, and viewing an existing task never replaces its browser.
If Google rejects sign-in while the person owns manual control, the preview
requests the disconnected sign-in mode once for that rejection. It does not
retry automatically after a failure or solve provider human-verification checks.

The handover waits for Chrome to close cleanly before reopening the same profile
for agent use. Both phases use the same installed Chrome executable, whose
identity is remembered for future launches. An older Chrome version is never
allowed to downgrade the profile. Existing browser data is retained; no cookies
are exported or copied and the person's ordinary Chrome profile is not opened.

Starting sign-in cancels the current browser task. Losing the viewer or failing
a transition leaves agent control paused until the person explicitly returns
it. A locked desktop, missing installed Chrome, or unsupported platform does not
silently switch to another browser. Ordinary **Take control** still pauses a task
without replacing its browser; **Sign in** performs the disconnected transition.

This does not promise that every service accepts automation after sign-in;
service-side restrictions and session revocation still apply. Google's
[supported browser guidance](https://support.google.com/accounts/answer/7675428)
documents the sign-in limitation. Website sign-in and Chrome profile/sync
sign-in are different flows; an X task does not require signing into Chrome Sync.

## Restart and login behavior

Profile assignments and pairing survive application restarts. Managed profile
directories are stable and are not deleted during browser shutdown. Chrome
retains its own website sessions independently of Jarvis. No always-running
service is needed to retain the assignment.

Persistent browser data does not guarantee permanent website authentication.
Websites can expire or revoke sessions and require MFA again. The UI reports
profile availability and connection state, not a cookie-file-based assertion
that a website is logged in.

For a profile connected through the extension, take manual control to sign in
directly in Chrome. During that connector's manual login,
the debugger is detached, captured previews are cleared, and model observations
stop. Losing the Jarvis viewer does not return control to the agent. Reclaim
control in the viewer and explicitly return it when finished.

## Connector boundaries

The extension creates a visible owned tab. It never adopts arbitrary user tabs,
copies profiles, exports cookies, or exposes arbitrary CDP commands to the
model. Permitted actions are DOM navigation, clicking, text input, scrolling
and waiting through the existing Jarvis model and ToolExecutor gates. File
upload/download, arbitrary JavaScript, cross-frame automation and switching
unrelated tabs are not supported by this connector. Managed-browser features
are unchanged.

The local connection uses exact loopback HTTP/WebSocket endpoints authenticated
with single-use pairing and installation-bound credentials. The general Jarvis
control key is never supplied to the extension. Website grants, owned-tab and
foreground checks, locked-desktop checks and single-use observations constrain
actions. On X, a visible account identity is required for clicks and input and
is rechecked before dispatch. A disconnected operation is never replayed.

## Verification scope

Focused backend and extension tests use fakes; frontend tests exercise sharing,
overrides, missing profiles, manual-login privacy and viewer reconnection. They
do not establish successful Google authentication, real X posting, actual
PC-reboot persistence, or desktop behavior on every operating system. In-window
login tests cover process arguments, Chrome identity/version checks, transition
failures, frame generations and explicit handover without launching a browser.
Real acceptance checks require the visible Jarvis browser or connected Chrome,
as appropriate, and must not be replaced with hidden/headless browser launches.
