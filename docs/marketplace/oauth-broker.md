# Publisher OAuth broker

RUB-99 introduces a separate publisher service for confidential OAuth clients.
It is not mounted by the desktop server. A local keyring is not a way to keep
a distributed application's confidential publisher secret confidential.

## Deployment contract

Run `python -m jarvis.marketplace.broker_service` on the publisher's private host,
behind HTTPS. It binds only loopback port 8799 and runs one process. Configure:

- `JARVIS_OAUTH_BROKER_BASE_URL`: the stable public HTTPS base address.
- `JARVIS_OAUTH_BROKER_DATABASE`: a persistent, publisher-owned SQLite file.
- `OAUTH_BROKER_ENCRYPTION_KEY`: a Fernet key supplied by the server secret manager.
- `PUBLISHER_<FAMILY>_OAUTH_CLIENT_ID` and
  `PUBLISHER_<FAMILY>_OAUTH_CLIENT_SECRET`: the registered publisher application's
  credentials, only on this service. The existing `get_secret` resolver is used.

Register the exact `<base>/callback` at each provider. Publish only the HTTPS base
in the corresponding catalog `auth.broker_url` after deployment verification.
The backend-only `publisher_oauth_broker_url` override supports staging.
Never ship publisher confidential secrets or an encryption key in desktop
settings, manifests, images, release archives, frontend payloads or logs.

The service does not imply that a public deployment exists. Until a real HTTPS
deployment, provider registration and lifecycle test are completed, the provider
remains BLOCKED. An operator must supply persistent storage, backup/key recovery,
HTTPS termination, request-size and abuse limits. Disable request-body/header and
query-string logging at the reverse proxy as well as the application. Do not
run multiple workers against this implementation: its refresh single-flight lock
is process-local. No paid service is provisioned by this implementation.

## Protocol

1. Desktop binds an ephemeral numeric loopback listener, generates a verifier
   and a separate desktop state, and sends its S256 challenge and callback
   binding to `POST /start`. Remote or ambiguous callback destinations are refused.
2. The service chooses a configured provider and its fixed scopes/endpoints.
   It creates independent provider state and, where supported, provider PKCE.
   Pending flows expire after five minutes; provider state and flow identifiers
   are hashed in SQLite, and pending data is encrypted.
3. The system browser opens the official provider. `/callback` validates and
   consumes the provider state, then exchanges the code on the service.
   It redirects the browser to that exact desktop listener with a one-time
   handoff code (at most 90 seconds) and the desktop state. Access tokens, refresh
   tokens and client secrets never enter this URL.
4. Desktop validates the local callback state and proves possession of BOTH
   the verifier and the browser-delivered handoff code through `POST /redeem`.
   Possession of the initiating flow/verifier alone cannot claim a grant approved
   in somebody else's browser. A completed
   flow is consumed once. The response contains an access token and an opaque
   refresh handle, never the provider refresh token or client secret.
5. Desktop stores its access token, handle, broker address and issuing public
   client ID together in the OS credential store. `POST /refresh` is bound to
   the provider and original client. Provider refresh rotation is committed to
   encrypted persistent storage before responding. A still-valid access token
   can be returned safely after a lost refresh response.
6. `POST /cancel` requires the original proof. `POST /disconnect` removes the
   encrypted service grant. This stops broker refresh; it is not an assertion
   that the provider immediately invalidated an already-issued access token.
   Provider account settings remain the authority for removing consent.

All responses are `no-store` and `no-referrer`. Errors contain fixed messages;
validation failures never echo submitted handles. Desktop validates the returned
authorization origin and path against the plugin's official authorization URL.
The broker desktop handoff requires the browser and Jarvis on the same host.
Remote-browser headless handoff is not attested by this implementation; it needs
a separately trusted hosted handoff before being advertised as supported. Base
headless boot does not depend on this service.

Existing local expert grants retain their original refresh path. A broker grant
retains its issuing broker address even if current configuration changes.

## Provider choices

| Provider family | Default architecture | Registration requirement |
| --- | --- | --- |
| Microsoft Graph and Azure | Public native PKCE, shared Microsoft client | Native `localhost` callback; tenant policy and publisher verification may limit consent |
| Slack | Public PKCE, user scopes, fixed loopback, token rotation | PKCE and rotation enabled in the official app console; public distribution remains separate |
| Zoom | Public Client PKCE | Enable the public-client option; confidential General OAuth is not interchangeable |
| GitLab | Non-confidential PKCE | Register loopback and uncheck confidential client |
| Spotify | Public PKCE | Premium developer account and provider access restrictions |
| Salesforce | Public PKCE when enabled for the application | Provider org and MCP entitlement/permissions |
| HubSpot, Asana, Figma, LinkedIn | Confidential broker | Registered HTTPS callback and server-side credentials |
| Discord | Broker identity plus official bot installation | Shared application, bot installation permissions, separately operated bot runtime |

Discord `Bearer` identity tokens are never used as gateway `Bot` tokens. The
broker authorization includes the official bot/app-command installation scopes
and the existing minimal permission bitfield. A completed identity grant does
not prove a running gateway or messaging. The existing local bot-token flow
remains explicitly expert-only; operating and verifying the shared bot relay
is still required for a complete normal-user messaging journey.

LinkedIn does document a native PKCE path, but requires a LinkedIn contact to
enable it. The general confidential path is used until such access is approved.
Spotify Development Mode requires Premium and limits clients to five users;
this is not unrestricted publisher distribution. No subscription was purchased.

## Capability state

Successful OAuth exchange is persisted before resource verification. The token
blob stores only a fixed `capability_state`, separately from `needs_reauth`:

| Resource result | Authentication | Capability |
| --- | --- | --- |
| Successful read | Connected | `live` |
| HTTP 401 | Needs reauthentication; grant retained | `unauthorized` |
| HTTP 403 | Connected | `limited` |
| HTTP 429 | Connected | `rate_limited` |
| Timeout, network or provider failure | Connected | `unavailable` |

The UI must not advertise Live for a failed capability probe. Saving an updated
health result checks that the grant has not meanwhile been disconnected or
replaced. Tests are regression evidence, not a substitute for the real browser
and functional audit. No SQL token schema is added: the existing protected
token JSON is the persistence boundary on Windows, macOS and Linux.

## Primary sources

- [Microsoft authorization code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)
- [Slack PKCE](https://docs.slack.dev/authentication/using-pkce/)
- [Zoom OAuth and public PKCE](https://developers.zoom.us/docs/integrations/oauth/)
- [Figma OAuth applications](https://developers.figma.com/docs/rest-api/oauth-apps/)
- [Asana OAuth](https://developers.asana.com/docs/oauth)
- [LinkedIn native clients](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow-native)
- [Spotify developer access restrictions](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security)
