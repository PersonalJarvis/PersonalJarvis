# Publisher OAuth broker on Cloudflare Workers

This is the persistent, public HTTPS deployment of the RUB-99 confidential
OAuth broker. It implements the same `/start`, `/callback`, `/redeem`, `/refresh`,
`/cancel` and `/disconnect` desktop protocol as `jarvis/marketplace/broker_service.py`.
The desktop never receives a provider client secret or refresh token. D1 rows
contain AES-GCM encrypted flow/grant payloads; IDs and provider states are hashed.
No provider is release-qualified merely because this broker is deployed.

The Worker and D1 database use features available in Cloudflare's Free plan. Its
custom domain is `https://oauth.personaljarvis.ai`; the account's personal
`workers.dev` hostname is disabled. The public `/healthz` route reveals only
availability. No payment method or paid upgrade was provisioned for RUB-99;
the existing account's billing-plan status has not been independently checked.

The D1 binding and public base URL are in `wrangler.toml`. The AES key and each
provider's `PUBLISHER_<FAMILY>_OAUTH_CLIENT_ID` and
`PUBLISHER_<FAMILY>_OAUTH_CLIENT_SECRET` are Cloudflare Worker secrets, set with
`wrangler secret put` from a secure operator channel. They must never be placed
in source, Wrangler vars, `.dev.vars`, CI output, issue comments or screenshots.
Slack is an explicit public-client exception: its Worker binding holds only
`PUBLISHER_SLACK_OAUTH_CLIENT_ID`, and token requests use PKCE without a secret.
Back up the AES key in a separate secret manager before rotating or moving the
deployment: losing it makes every encrypted grant unrecoverable. Rotate a
provider secret only together with its issuing-client and grant migration plan.

Initialize a fresh database with `wrangler d1 execute personaljarvis-oauth-broker
--remote --file=./schema.sql`, then deploy from this directory with
`wrangler deploy`. Keep Worker Observability and query-string/request-body
logging disabled: an OAuth callback contains a short-lived authorization code.
Each provider must register exactly `https://oauth.personaljarvis.ai/callback`
and have its ID and secret provisioned before adding `broker_url` to the shipped
plugin catalog. A `/start` returning 503 means that provider registration is
still missing. The broker has a per-IP start limit and a one-use browser-to-
loopback handoff; authorization and refresh are bound to the issuing client.

Run `node --test test/broker.test.mjs` for protocol regressions and
`wrangler deploy --dry-run` for bundle validation. These checks use dummy
credentials; they do not establish any live provider PASS. Full qualification
still requires official browser login/consent, callback, read-only plugin use,
disconnect/reconnect and restart through the real Personal Jarvis Plugins UI.

Primary provider architecture and local fallback are documented in
`docs/marketplace/oauth-broker.md`.
