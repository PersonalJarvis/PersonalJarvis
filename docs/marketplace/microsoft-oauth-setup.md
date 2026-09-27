# Microsoft sign-in for Outlook, OneDrive, Teams, SharePoint, OneNote, To Do and Azure

A normal user clicks **Connect**, signs in at Microsoft in the browser, and
approves the permissions for that plugin. No Azure portal, no app
registration, no client id and no client secret.

All seven plugins share one public sign-in that ships with Jarvis
(`SHIPPED_PUBLIC_CLIENT_IDS` in `jarvis/marketplace/publisher_clients.py`).
The Entra app is named **Personal Jarvis**. It accepts personal Microsoft
accounts and work or school accounts, uses Authorization Code + PKCE, and
has no client secret. The only redirect address is
`http://127.0.0.1:43891/oauth/callback`.

| Plugin | What the sign-in asks for | Account |
|---|---|---|
| Outlook Mail & Calendar | `offline_access User.Read Mail.Read Mail.Send Calendars.ReadWrite` | Personal or work/school |
| OneDrive | `offline_access User.Read Files.ReadWrite` | Personal or work/school |
| Microsoft Teams | `offline_access User.Read Chat.Read ChatMessage.Send OnlineMeetings.ReadWrite` | Work/school |
| SharePoint | `offline_access User.Read Sites.Read.All Files.Read.All` | Work/school |
| OneNote | `offline_access User.Read Notes.ReadWrite` | Personal or work/school |
| Microsoft To Do | `offline_access User.Read Tasks.ReadWrite` | Personal or work/school |
| Microsoft Azure | `offline_access https://management.azure.com/user_impersonation` | Work/school, plus permission to read that subscription. Cost figures also need Cost Management Reader or an equivalent role. |

Microsoft may show the app as an unverified publisher until the project
completes Microsoft's publisher verification. That screen is still the
normal browser sign-in: the user can approve it. A company directory can
also require an administrator to approve Personal Jarvis once. That is an
account policy, not setup the user has to perform in Azure.

## Expert override

Someone who wants a different Entra app can still bring their own public
client. Click **Connect**, open **Use your own OAuth client**, paste the
Application (client) ID, and leave the secret empty. Jarvis stores it as
`microsoft_oauth_client_id`. One id covers all seven plugins. It wins over
the shipped client, so a mistake here replaces the shared sign-in until the
secret is removed.

The replacement app needs the same redirect address, the delegated
permissions of the plugins that will use it, and the account type
**Accounts in any organizational directory and personal Microsoft accounts**.
Do not create a client secret.

A distribution can also set `publisher_microsoft_oauth_client_id` (or
`PUBLISHER_MICROSOFT_OAUTH_CLIENT_ID`) to point every user at a different
public client without editing the catalog. An empty secret never replaces
a real one.

## Troubleshooting

| Symptom | Meaning | Fix |
|---|---|---|
| `redirect_uri_mismatch` | The app's redirect address differs by even one character | The shipped app already uses `http://127.0.0.1:43891/oauth/callback`. A custom app must use that exact address. |
| Consent / admin-approval error (Teams, SharePoint, Azure, or a locked company directory) | The directory requires an administrator to approve the app | An administrator approves Personal Jarvis once, then the user connects again. |
| Personal account refused (Teams, SharePoint, Azure) | Those plugins need a work or school account | Sign in with a work or school account. |
| Unverified-publisher warning | Microsoft has not verified the app's publisher identity | The sign-in still works. Verification is a separate publisher step. |
| Callback port busy | Another sign-in already holds port 43891 | Finish or cancel the other Connect first, then retry. |
