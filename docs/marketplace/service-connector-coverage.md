# Service connector coverage

Historical implementation inventory from 2026-09-10. The authentication
prerequisites below describe that earlier implementation, not current defaults.
Use [the current browser audit](plugin-auth-audit.md) for connection behavior,
verified evidence and outstanding publisher/account requirements.

Implementation tier: **T3** (new service providers and local-device authentication).

The expansion adds 22 entries to the existing 24-plugin catalog. Existing Slack,
Discord, GitHub, Supabase, Cloudflare, Notion, Dropbox and Stripe entries are reused.
Every new entry includes an Agent Plugins package, a paired skill, a usage card,
an original brand asset and an in-app connection flow.

## Implemented scope and prerequisites

| Plugin | Implemented operations | Account/setup requirements and limits |
|---|---|---|
| Outlook Mail & Calendar | Read, send, reply; list/create/update events | Microsoft delegated Graph consent; own Entra public client |
| OneDrive | List/search files, upload bytes, create sharing links | Files.ReadWrite; uploads through this connector up to 20 MiB |
| Teams | List chats/messages, send chat messages, create meetings | Work/school account; returns meeting join links, does not ring participants |
| SharePoint | Search sites/documents, read libraries/lists/items | Work/school account; Sites.Read.All and Files.Read.All; tenant consent |
| OneNote | Read notebooks/sections/pages, create and update pages | Notes.ReadWrite; HTML page content and Graph patch commands |
| Microsoft To Do | List lists/tasks, create and update tasks | Tasks.ReadWrite; completion uses the existing task's status |
| AWS | Official AWS MCP resource and cost tools | IAM permissions and AWS OAuth policy; Frankfurt endpoint; single account per connection |
| Azure | List subscriptions/resources, query actual costs | Azure Service Management delegated access; billing read permissions |
| Google Cloud | Projects, buckets/objects, billing accounts, billing-export queries | Enabled APIs; actual spend requires a preconfigured BigQuery export; query charges can apply |
| GitLab | Projects, issues, merge requests and comments | GitLab.com PAT; api scope for writes; self-managed instances are not included |
| AMD GPU Status | AMD SMI static and metric JSON | Optional supported AMD hardware/CLI; connect probes actual data; native Windows/macOS unavailable |
| AgentMail | Official MCP inbox/message/thread/draft tools | Own AgentMail API key; no personal skill credentials are imported |
| X | Profile, posts, mentions, publish/reply | Own native OAuth app; X API entitlement and usage charges |
| LinkedIn | Own profile and publishing posts | Own app with OpenID Connect and Share on LinkedIn; general private inbox, contacts and feed reads are unavailable |
| Meta | Facebook Page posts/comments; Instagram professional publishing/comments | Long-lived user token, Page roles, reviewed app permissions; no personal-account posting |
| YouTube Studio | Video uploads, channel counters, analytics, comments and replies | Own Google OAuth client and enabled APIs; private upload default; project audit and channel permissions apply |
| HubSpot | Read contacts/companies/deals; search contacts | Private app with the three CRM object read scopes |
| Apollo.io | Official MCP company/contact search and enrichment | Apollo account and credits; free personal-email accounts have restricted access |
| Salesforce | Official sobject-all hosted MCP operations | Admin activation and own External Client App; production org endpoint |
| Granola | Official MCP meeting notes and transcript tools | Granola account with access to the requested notes |
| Zoom | List/create meetings, list recordings/transcript links | Own user-managed OAuth app; cloud recordings/transcripts must already exist; meeting creation returns a link |
| Figma | Projects/files/design data/comments | Figma PAT with current_user:read, projects:read, file_content:read and file_comments:read |

**Unresolved request:** "Datadoc (data catalog)" does not uniquely identify a
provider. No invented endpoint or nonfunctional placeholder is shipped. A product
URL is needed before implementing that item.

## Verification boundaries

Verification on 2026-09-10: 581 backend/marketplace/CLI tests and 45 frontend
tests passed; focused contracts were rerun after final fixes. The production
frontend built successfully. All new manifests validated against the published
Agent Plugins 1.0.0 schemas. The installed wheel contained 46 catalog entries
and 22 new packages, and its real stdio handshake plus error propagation passed
with one synthetic credential and no outgoing provider request. Browser checks
covered family search, Microsoft/AgentMail/Figma setup, both themes and missing
image/error detection. Cold boot measured 2.9 s to window, 16.5 s to interactive
and 16.6 s to usable voice, within the configured budgets.

Contract tests verify fixed provider hosts, HTTP methods, path escaping, body
encoding, authentication headers, error redaction, no automatic write retries,
token rotation/disconnection and capability degradation. Integration tests cover
the catalog, registry, paired skills, usage cards, secrets and connection routes.
These tests use fake credentials and HTTP transports; they are not proof that a
particular external account has consented or has the necessary subscription.

Public protected-resource and authorization metadata were inspected for AWS,
AgentMail, Apollo, Granola and Salesforce. Live account operations require a user
to connect the relevant account. No external mail, social posts or cloud-resource
changes are made as test traffic.

Windows, macOS and Linux use the same HTTP and OAuth implementations. The live
app runs REST tools in process and reads current stored credentials for each
operation. Worker clients use the packaged stdio bridge with the app's Python
interpreter. No cloud SDK, vendor desktop app or GPU library is required to boot.
AMD SMI is probed only when connecting or explicitly requesting device data.
Headless OAuth uses the existing configured hosted callback.

An already running backend retains its loaded Python modules and catalog. A
frontend rebuild alone cannot activate a new backend connector; the desktop
agents may restart the application when needed for authorized implementation or
verification, announce the reason, and verify that it returns healthy.

## Provider references

- [Microsoft delegated Graph access](https://learn.microsoft.com/en-us/graph/auth-v2-user)
- [Teams chat messages](https://learn.microsoft.com/en-us/graph/api/chat-post-messages?view=graph-rest-1.0)
- [AWS MCP setup and OAuth limits](https://docs.aws.amazon.com/agent-toolkit/latest/userguide/getting-started-aws-mcp-server.html)
- [Azure Cost Management API](https://learn.microsoft.com/en-us/rest/api/cost-management/)
- [Google Cloud billing exports](https://cloud.google.com/billing/docs/how-to/export-data-bigquery)
- [GitLab REST API](https://docs.gitlab.com/api/rest/)
- [AMD SMI CLI](https://rocm.docs.amd.com/projects/amdsmi/en/latest/how-to/amdsmi-cli-tool.html)
- [AgentMail MCP](https://docs.agentmail.to/integrations/mcp)
- [X OAuth PKCE](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code)
- [LinkedIn publishing](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin)
- [Meta Instagram publishing](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/content-publishing/)
- [YouTube Data API](https://developers.google.com/youtube/v3/docs)
- [HubSpot CRM API](https://developers.hubspot.com/docs/api-reference/crm-contacts-v3/guide)
- [Apollo MCP](https://docs.apollo.io/docs/apollo-mcp)
- [Salesforce hosted MCP](https://developer.salesforce.com/docs/platform/hosted-mcp-servers/guide/hosted-mcp-servers-overview.html)
- [Granola MCP](https://www.granola.ai/blog/granola-mcp)
- [Zoom API](https://developers.zoom.us/docs/api/)
- [Figma scopes](https://developers.figma.com/docs/rest-api/scopes/)
