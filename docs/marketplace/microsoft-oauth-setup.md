# OAuth app setup for the Microsoft plugins (Outlook, OneDrive, Teams, SharePoint, OneNote, To Do, Azure)

> Standard notice (see `browser-auth-standard.md`): the product standard is
> that the PROJECT provisions the shared OAuth client and the user only
> clicks Connect. That publisher registration is still open (tracked in
> `plugin-auth-audit.md`, family `microsoft`), so until it lands, the expert
> path below is the only working route. It is documented honestly as an
> expert option — not as the standard.

These marketplace plugins use browser-login OAuth (Authorization Code + PKCE
loopback) against an app **you** register once in Microsoft Entra. This is
Microsoft's security model — no one can do it for you, and there is no shared
Jarvis-owned client yet: every user connects their *own* Microsoft account
through their *own* Entra app registration.

No client secret is needed. Entra public/native clients complete the PKCE flow
with no secret, and Jarvis omits the secret from the token exchange and the
refresh when none is stored. If you paste a secret anyway it is kept, but for a
public app the secret field stays empty.

One registration covers all seven plugins. They share a single OAuth client
family (`microsoft`), a single redirect URI, and a single placeholder:

| Plugin | Scopes requested | Account note |
|---|---|---|
| Outlook Mail & Calendar | `offline_access User.Read Mail.Read Mail.Send Calendars.ReadWrite` | Personal or work/school |
| OneDrive | `offline_access User.Read Files.ReadWrite` | Personal or work/school |
| Microsoft Teams | `offline_access User.Read Chat.Read ChatMessage.Send OnlineMeetings.ReadWrite` | Work/school required |
| SharePoint | `offline_access User.Read Sites.Read.All Files.Read.All` | Work/school required |
| OneNote | `offline_access User.Read Notes.ReadWrite` | Personal or work/school |
| Microsoft To Do | `offline_access User.Read Tasks.ReadWrite` | Personal or work/school |
| Microsoft Azure | `offline_access https://management.azure.com/user_impersonation` | Work/school required, plus Azure Service Management permission and Cost Management Reader (or equivalent) for billing |

Shared values for all seven:

- Redirect URI (character for character): `http://127.0.0.1:43891/oauth/callback`
- Catalog placeholder to replace: `REPLACE_WITH_YOUR_CLIENT_ID`
- Secret slot: none needed (leave the secret field empty)

---

## Part A — Your own Entra app (the working route today)

1. Go to <https://entra.microsoft.com/> → **Identity → Applications → App registrations** → **New registration**.
2. Name: `Personal Jarvis` (anything recognizable works).
3. Supported account types: **Accounts in any organizational directory (any Microsoft Entra ID tenant - Multitenant) and personal Microsoft accounts**. This one choice is what lets the same client serve both `/common` plugins (Outlook, OneDrive, Teams, SharePoint, OneNote, To Do) and the `/organizations` plugin (Azure).
4. Redirect URI: platform **Public client/native (Mobile & desktop)**, address `http://127.0.0.1:43891/oauth/callback`. It must be the numeric address (not `localhost`), with the `/oauth/callback` path and no trailing slash. A mismatch ends Connect with `redirect_uri_mismatch`.
5. **Register** → copy the **Application (client) ID** (a GUID like `12345678-90ab-cdef-1234-567890abcdef`). This is the only value Jarvis needs.
6. **API permissions → Add a permission → Microsoft Graph → Delegated permissions** → add only what you need from this union: `offline_access`, `User.Read`, `Mail.Read`, `Mail.Send`, `Calendars.ReadWrite`, `Files.ReadWrite`, `Files.Read.All`, `Chat.Read`, `ChatMessage.Send`, `OnlineMeetings.ReadWrite`, `Sites.Read.All`, `Notes.ReadWrite`, `Tasks.ReadWrite`.
7. For Azure only: **Add a permission → APIs my organization uses → Azure Service Management → Delegated permissions → `user_impersonation`**.
8. If your tenant requires it, an administrator must click **Grant admin consent**. Without it, Teams/SharePoint/Azure sign-in ends on a consent error — that page means exactly this missing approval, not a Jarvis bug.
9. Do NOT create a client secret. Under **Certificates & secrets** nothing needs to be added for a public client.
10. Give the Client ID to Jarvis. **Preferred: in the app** — click **Connect** on the plugin → expand **"Your OAuth client"** → paste the Application (client) ID → leave **Client Secret** empty → **Continue**. Jarvis stores the ID as the `microsoft_oauth_client_id` secret for you (shared by all seven plugins — enter it once) and opens the real Microsoft login in your browser. Sign in, approve the requested permissions, and the plugin verifies itself before showing Connected.

## Part B — Publisher-shared client (the future one-click route)

For the maintainer, once: perform Part A once on the project's own Entra tenant with the full permission union and the multitenant account type, create no secret (public client), then provision only the ID:

```powershell
# env fallback (simplest; works headless / VPS)
$env:PUBLISHER_MICROSOFT_OAUTH_CLIENT_ID = "12345678-90ab-cdef-1234-567890abcdef"
```

or store it permanently in the credential manager (service `personal-jarvis`), key `publisher_microsoft_oauth_client_id`. No `publisher_microsoft_oauth_client_secret` value is needed for a public client; an empty secret never displaces anything. From that moment every user gets one-click browser login on all seven plugins with zero setup, and the own-client dialog collapses to the expert override it was designed to be.

---

## Applying Client IDs

Three ways, in precedence order:

1. **In-app, in the Connect dialog (recommended).** Click **Connect** (or **Reconnect**) → paste the Application (client) ID, secret empty. Jarvis writes `microsoft_oauth_client_id` for you — no env vars, no file edits, no restart. One ID covers all seven Microsoft plugins.
2. **Secret directly (headless / scripted).** Set the credential-manager secret or env var yourself — `microsoft_oauth_client_id` / `MICROSOFT_OAUTH_CLIENT_ID`. It overrides the catalog at connect-time *and* refresh-time and survives a catalog re-sync.
3. **`data/plugin_catalog.json` (fallback).** Your local, gitignored runtime override; edit the seven entries and restart. Note this file is re-synced from the seed, so a real Client ID written here can be reset back to the placeholder — prefer a secret.

After any change, restart Jarvis and connect the plugin. The tracked `jarvis/marketplace/seed_catalog.json` and `jarvis/marketplace/plugins/*/plugin.json` keep the placeholders (never commit your real Client ID).

## Troubleshooting

| Symptom | Meaning | Fix |
|---|---|---|
| `redirect_uri_mismatch` / provider rejects the login | The registered redirect URI differs by even one character | Re-register exactly `http://127.0.0.1:43891/oauth/callback` (numeric IP, path included, no trailing slash) |
| Consent / admin-approval error (Teams, SharePoint, Azure) | Tenant policy requires administrator consent | An admin grants consent in Entra → reconnect |
| Personal account refused (Teams, SharePoint, Azure) | Those plugins genuinely need a work/school account | Sign in with a work/school account |
| Callback port busy | Another sign-in already holds port 43891 | Finish or cancel the other Connect first, then retry |
| Reconnect needed after 7+ days | Testing-mode or policy expiry of the grant | Reconnect; for durable grants, ask the admin about the app's audience/policies |
