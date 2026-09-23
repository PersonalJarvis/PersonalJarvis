# RUB-99 observed authentication evidence

## Continuation checkpoint (2026-09-23)

The user reports completing the Microsoft ecosystem independently. Preserve
those connections and exclude Microsoft from this continuation. This report
does not replace the historical observations below with a new agent-observed
PASS. GitLab's existing complete Windows attestation is unchanged.

Remaining provider work: HubSpot, Salesforce, Discord, Zoom, Asana, Figma,
LinkedIn, Spotify and the newly added Shopify plugin, plus Slack public distribution and its remaining lifecycle
checks. The confidential broker still needs a stable HTTPS publisher deployment,
server-only credentials and real provider qualification. No deployed broker was
established during this checkpoint; repository deployment guidance alone is not
deployment evidence.

Later on 2026-09-23, a Cloudflare Workers/D1 broker using Free-plan-eligible
features was deployed at the
project-owned `oauth.personaljarvis.ai` HTTPS domain. Its real `/healthz` request
returned HTTP 200; an unconfigured Asana `/start` correctly returned HTTP 503.
Its worker code has protocol regression tests, encrypted D1 persistence and
server-only secret bindings. This resolves only the hosting prerequisite: no
provider secret, consent, callback or resource read has been qualified yet.
The earlier sentence above records the state at the initial checkpoint.

Shopify was added to the main catalog on 2026-09-21 and has been included in
this RUB-99 branch. A real unauthenticated MCP `tools/list` returned tool names,
but a real read-only `get-shop-info` request returned HTTP 401 with an official
OAuth resource-metadata pointer. The metadata confirms the configured authorize
and token endpoints, PKCE S256, and `token_endpoint_auth_methods_supported =
["none"]`, so a public PKCE client is appropriate. It does not publish Dynamic
Client Registration. The four requested read scopes are absent from the current
published `scopes_supported` list, so consent and resource access remain
unverified. A free app registration is still pending.

Chrome automation was restored later by correcting the local runtime mapping
and native-host registration under the user's express instruction. The existing
Asana app was opened in the official console; any-workspace distribution is
selected, but no redirect is registered yet. Its console states that an MCP app
automatically requests full access to all Asana resources and actions. The
Figma account signed in using the requested Google identity, and a new
Personal Jarvis app is prepared under the existing team. App creation would
accept Figma developer terms, so it remains pending explicit action-time consent.
Neither provider is marked connected.

The current Chrome automation bootstrap fails before a browser can be selected:
the installed runtime requests a missing browser-service module. The official
plugin diagnostics confirm that Chrome is running and the extension is enabled,
but its Windows native-host registration is missing. The plugin's documented
recovery is reinstalling the Browser/Chrome plugin through the application UI;
manual native-host repair is explicitly prohibited by its instructions.

Consequently, no new provider login, consent, callback, resource read, reconnect
or restart was observed on this date. No provider result or stage is promoted.
This is a browser-tooling blocker, not evidence of a provider restriction.
Resume provider provisioning and the real Plugins UI journeys after that
connection is restored. Microsoft grants and the running desktop were untouched.

Validation at this checkpoint: 112 focused broker, PKCE, client-family,
capability-state, refresh, restart and audit-gate regression tests passed. The
structural authentication gate passed for all 46 catalog plugins. These checks
validate existing implementation behavior and audit structure, not live OAuth.

## Earlier browser observations (2026-09-19)

Observed on 2026-09-19 in Chrome, using the actual Personal Jarvis Plugins UI
served by an isolated Windows development instance. Existing unrelated grants
were retained. Target scope: 18 integrations, with one complete Windows PASS
and 17 incomplete full-provider qualifications. Provider accounts were not simulated. Automated transports used
in regression tests are explicitly excluded from this browser evidence.

## GitLab

**Full Windows journey PASS.** A real Personal Jarvis OAuth application was
created in the official GitLab console with Confidential disabled, the fixed
IPv4 loopback callback and the connector's API scope. Its public identifier is
shipped in the catalog; no client secret is used by the desktop path.

The plugin started unconnected. Connect opened GitLab, the official Personal
Jarvis consent completed, and Jarvis persisted the grant and showed Connected.
The real UI Remove action returned it to Connect. A second Connect and official
consent restored Connected. After restarting the isolated backend, the real
detail view still showed Connected / Live and Publisher public client.
Clicking **Check access** then displayed **Read-only check passed**. That product
action executes the connector's real `list_projects` operation, limited to one
entry; it is not tool discovery or an external API stand-in.

This is a Windows browser attestation. Linux protocol contracts passed separately;
native macOS browser/keychain behavior was not exercised on this host.

## Google Cloud

The original failure was reproduced: official Google sign-in and consent returned
to the loopback listener, but the old runtime rejected the connection when the
resource check failed. In the changed runtime the same real journey persisted
the grant and the detail view displayed **Connected / Limited access**. That
grant survived a backend restart.

The explicit UI Check access action initially reported restricted access while
preserving authentication. A sanitized diagnostic identified `SERVICE_DISABLED`
for Cloud Resource Manager, not insufficient OAuth scope. The official Google
console confirmed the API was disabled in the existing OAuth project's account.
It was activated there without adding payment details or purchasing anything.
The console then reported Enabled. A subsequent **Check access** in the real
Plugins UI displayed **Read-only check passed**, executing `projects:search`.
No BigQuery query, paid resource creation or billable storage action was run.

This used the existing Google family client already configured on the machine.
A real Remove / Connect cycle was also completed, restoring Connected after
new official Google consent and callback.

It does not establish a newly shipped publisher Google client or fresh-account
distribution. Full-plugin release qualification remains separate from the
observed state-machine repair and successful resource read.

Source for the no-charge operation:
[Google Resource Manager pricing](https://cloud.google.com/resource-manager/pricing).

## Microsoft family

A real Personal Jarvis public/native application was created in the official
Microsoft console, supporting organizational and personal accounts, with the
native `localhost` callback. No client secret was created. Its public identifier
is shared by the seven Microsoft catalog entries.

| Plugin | Observed journey and access state | Remaining acceptance |
| --- | --- | --- |
| Outlook | UI Connect completed, persisted grant, fixed read-only mail probe succeeded | Full independent post-login smoke and disconnect/reconnect cycle not completed |
| OneDrive | Official file permission consent accepted; callback persisted; fixed file-list probe succeeded | Full independent post-login smoke and disconnect/reconnect cycle not completed |
| OneNote | Official notebook permission consent accepted; callback persisted; fixed notebook-list probe succeeded | Full independent post-login smoke and disconnect/reconnect cycle not completed |
| Microsoft To Do | UI Connect completed; persisted grant; fixed list probe succeeded | Full independent post-login smoke and disconnect/reconnect cycle not completed |
| Teams | Browser grant persisted; resource probe returned 401 and the UI initially required reconnect | Valid Teams resource access and full lifecycle not established |
| SharePoint | Browser grant persisted; resource capability unavailable for the test account | SharePoint resource access and full lifecycle not established |
| Azure | The original organizations authority explicitly rejected a personal account | Authority now matches the publisher app's common audience; live retry and resource acceptance remain pending |

The Microsoft console additionally warned about verified publishers for
cross-tenant end-user consent. Creating the application is not publisher
verification. No business identity, paid verification, subscription or billing
method was fabricated or purchased.

## Slack

A real Personal Jarvis public PKCE application was created in the official
console. The fixed loopback redirect, user scopes, token rotation and MCP setting
were saved and verified after reloading the console. Connect in Jarvis opened
the official permission page; Allow returned to Jarvis and produced Connected /
Live. The protected grant has a refresh token and no client secret. After a
backend restart, **Check access** in the real plugin detail view displayed
**Read-only check passed** using Slack `conversations.list`, limited to one public
channel. This is a real resource read, not MCP tool discovery.

Public distribution is still blocked: the official Manage Distribution page
keeps Activate Public Distribution disabled and specifically requires HTTPS
redirect URLs. Its native HTTP loopback login works for the development workspace,
but that must not be advertised as unrestricted distribution. A stable publisher
HTTPS rendezvous and a full disconnect/reconnect retest remain outstanding.
The code also corrects Slack revocation to use its documented access-token
contract and check JSON success instead of accepting any HTTP 200.

## Confidential providers and remaining external setup

| Provider | Actual external observation | Outstanding work |
| --- | --- | --- |
| Asana | Free account and Personal Jarvis MCP application created; any-workspace distribution selected and submitted | Stable publisher HTTPS broker, exact callback, server-only credential provisioning and full UI journey |
| Discord | Existing PersonalJarvis application found in the official developer console and its OAuth section opened | Stable broker callback and shared bot operation/installation verification; identity is not gateway messaging |
| HubSpot | Official login reported no account for the test identity; free signup reached Google account selection | Finish account/app provisioning, stable broker deployment and complete provider journey |
| Figma | Official developer app page and Google account selection reached | Chrome repeatedly failed to activate the account picker; no completed login, app provisioning or broker journey |
| LinkedIn | Official developer login and Google sign-in selection reached | Account/app prerequisites, provider products/approval, broker callback and complete journey |
| Salesforce | Official sign-in and email-identification page reached | Authenticated provider org, public PKCE application and MCP access still unverified |
| Zoom | Official Marketplace sign-in attempted with Google; provider returned error 300 | Complete provider sign-in, provision Public Client PKCE and verify the plugin journey |
| Spotify | Official Google and email login both reported no linked account for the test identity | An existing eligible Premium developer account and provider distribution approval; no paid upgrade was made |

These are BLOCKED, not PASS. The standalone confidential broker implementation
has not been deployed publicly and its real provider flows have not been
attested. Its automated protocol tests cannot replace those missing observations.
See [the deployment and protocol contract](oauth-broker.md).

Official constraints:
[Spotify developer access](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security),
[Asana MCP registration](https://developers.asana.com/docs/integrating-with-asanas-mcp-server),
[LinkedIn native PKCE enablement](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow-native),
[Slack token revocation](https://docs.slack.dev/reference/methods/auth.revoke/).

## Verification and limitations

Recorded checks: 850 Windows marketplace/auth/setup tests, 276 CLI tests,
56 frontend tests (including the capability/probe cases), 595 core guard tests,
and 44 Linux auth contract tests passed. The production frontend build, targeted
auth typing/lint checks and CLI coverage passed. The structural auth audit is
clean; strict full-inventory release qualification still rejects the 45 BLOCKED
plugins, as intended.

The implementation has Windows Python regressions for PKCE/callbacks, publisher
resolution, broker exchange/refresh/restart/cancellation, credential boundaries,
Google capability outcomes, refresh rechecking, and the explicit UI probe.
Linux container contracts exercise the same portable auth code without network
access to real providers. Frontend tests distinguish Connected from Live and keep
publisher setup out of the normal user's credential form. CLI coverage verifies
the mounted probe route and `jarvis marketplace verify` command.

Chrome intermittently failed to dispatch actions on existing tabs, including
target attachment and screenshot commands. Fresh tabs recovered some operations,
but not every remaining account/lifecycle step. These execution failures are not
provider approvals. Historical Connect-entry observations in the older audit are
retained as historical observations, not relabeled as successful current flows.

No secrets, raw provider error payloads, emails, machine paths or account-owned
resource identifiers are included in this public evidence record. No paid plan,
API-credit purchase, paid upgrade or payment method was added.
