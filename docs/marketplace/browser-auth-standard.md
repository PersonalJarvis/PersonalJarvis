# Browser-Auth Standard for Marketplace Plugins

Status: binding for all new and changed plugin auth. Companion: the
per-plugin audit in `plugin-auth-audit.md` (which plugin is standard-ready,
which is externally blocked, and why).

## 1. The standard flow

Plugin selected → "Connect" → the OFFICIAL provider opens in the browser →
user signs in and approves → Jarvis captures the callback automatically →
plugin is connected AND usable.

Normal users MUST NOT have to:

- create, copy, or paste tokens or API keys;
- provide client IDs, client secrets, or credential files;
- create their own OAuth apps, developer projects, or developer orgs;
- configure redirect URIs, env vars, or config files.

Internal tokens still exist. Jarvis obtains, stores (OS credential store),
and refreshes them automatically. This standard abolishes MANUAL credential
setup, not technical authentication.

Sign-in happens at the real provider. No rebuilt login forms, no password
or cookie harvesting, no foreign OAuth client identities.

## 2. How a plugin satisfies the standard

Pick the officially supported flow for the provider:

- `dcr` — hosted MCP + Dynamic Client Registration + PKCE (reference path;
  no static client at all). Preferred wherever the provider offers it.
- `publisher_pkce` — Authorization Code + PKCE loopback against the
  publisher-provisioned shared client (`publisher_<family>_oauth_*`
  secrets, see `jarvis/marketplace/publisher_clients.py`).
- `device` — official provider device flow. At most the intended
  short-lived confirmation code, never a self-made API token.
- `hosted` — server-side OAuth via the hosted callback (only where the
  provider architecture genuinely needs it).

Rules:

- The app registration and public client configuration are provided by the
  PROJECT (publisher), not by each end user. Real, correctly configured
  registrations only — no invented client IDs, no placeholders that end up
  back in a user form.
- Public client IDs and confidential secrets are distinguished. No
  confidential secret in the repo, frontend, or shipped desktop bundle.
- A central service is used only where the provider architecture genuinely
  needs one — never forced onto local desktop flows.
- Least privilege scopes. No credential material in logs or error messages
  (use the `ERROR_*` codes + `sanitize_provider_error`).
- Cancel, timeout, retry, reconnect, disconnect. Understandable errors for
  denied consent, missing admin approval, and provider outages.
- "Connected" is shown only after authorization completed AND a
  side-effect-free functional check passed. An opened browser tab or a
  merely stored token is not enough.
- Existing working connections migrate cleanly. Required re-logins go
  through the new browser flow. Own OAuth clients stay, at most, a clearly
  separated expert option — never the default prerequisite.
- While a publisher client is still pending, a browser-primary plugin may
  keep its previously working token path as a collapsed dialog fallback
  (`fallback_auth`, PAT-only). The fallback is an expert bridge that keeps
  the plugin usable — it never becomes the advertised default, and it
  disappears from the dialog once the shared login is ready.

Plugins with no auth need stay login-free. Normal account prerequisites
(and corporate admin approvals) are distinct from developer setup.

## 3. Honesty about external dependencies

Full coverage is the goal, but no invented provider capabilities. Where a
provider offers no suitable official browser path, say so in the audit
(web-Key-then-paste does NOT satisfy the standard). Where real app
registrations, publisher verifications, approvals, or accounts are missing,
implement everything independently achievable and name the remaining
blocker precisely — including who must act. Never bypass security
approvals; never trigger paid or legally binding steps unauthorized.

An externally blocked plugin is NOT done, live-tested, or standard-ready.
Hiding, removing, or disabling a broken plugin is NOT a repair.

## 4. Release gate for new auth-bearing plugins

A new plugin that needs an account is NOT standard-ready / releasable when
its normal user path needs manual tokens, client IDs, secrets, or
developer registrations. The check inspects the actual connect flow and
required configuration — not a metadata flag. Additionally document a real
provider verification as a release precondition where it cannot be
reliably automated.

Enforced by:

- `jarvis/marketplace/catalog.py` — discriminated browser, device and
  instance-address authorization configuration;
- `jarvis/marketplace/publisher_clients.py` — shared resolution;
- `jarvis/marketplace/connection_verification.py` — bounded resource checks
  before publishing a successful OAuth connection; unknown hooks fail closed;
- `tests/contract/test_plugin_auth_standard.py` — mandatory contract tests;
- `scripts/ci/check_plugin_auth_contract.py` — blocking configuration and
  browser-evidence gate. `plugin-e2e-audit.json` records all seven observed
  stages, a real action for each smoke PASS, concrete blockers, and a fingerprint
  of each plugin's auth and execution configuration. New built-ins require PASS;
  `--require-e2e-pass` rejects every remaining blocker for release qualification.

Evidence is an attestation of an observed provider journey. A schema check cannot
perform account consent. Neither an opened page, a successful mock, nor MCP tool
discovery qualifies as a real action or complete E2E PASS. Configuration changes
invalidate the previous audit fingerprint and require fresh evidence.
