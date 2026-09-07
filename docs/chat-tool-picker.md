# Chat tool picker

T3: a per-message selection contract from the composer to the runtime and persisted event log.

## Behavior

The Jarvis chat's **Add** menu offers file attachment, a working folder, and a
searchable inventory. It groups plugins by service and MCP operations by server,
with separate filters for skills, memory/knowledge, web/research, files,
automations, system tools, and CLIs. Select or remove multiple chips, then send
a normal message. Selections apply to that message only. The saved user bubble
retains the chips when the chat is reopened. A selection is a request to use a
capability when applicable, not proof it ran; actual calls have separate tool
results and retain the existing approval cards.

The picker is specific to the Jarvis surface. Coding-agent, society and setup
chats retain their own tool and permission contracts. Existing slash completion,
file attachment, dictation, model choices and permission controls remain usable.

## Inventory and provenance

The live brain tool dictionary is authoritative for executable tools. The
marketplace catalog contributes service names, descriptions and unavailable
plugin rows; native connectors also require a usable saved connection. The MCP
registry contributes configured but disconnected servers. The active skill
registry contributes executable skill slugs. Draft skills cannot be selected.
Folder tools are built for the session's actual working directory. Tools with
static risk `block` are omitted. Plan mode restricts the catalog and the final
tool dictionary using the same existing read-only filter.

This deliberately does not enumerate tools from the coding assistant's own
plugins or from a developer's personal configuration. Arbitrary installed MCP
operations and active skills are discovered at runtime, so a hardcoded list
would go stale immediately. The shipped inventory below is a source snapshot;
not every entry is necessarily loaded on every installation.

Original service marks are reused from `jarvis/ui/web/frontend/src/assets/brands/`
with their existing `LOGOS.md` provenance ledger, including full-color Gmail.
Unknown brands receive a category glyph and text. No logo network request is
needed to open the picker.

## Search

Empty search browses the inventory without a model call. Typing is debounced by
600 ms. Nonempty queries use the chat's selected provider/model and existing
Agents credential resolution, through `Brain.complete` with an empty tool list,
no history and a ranking-only system instruction. Search metadata and queries
are explicitly untrusted data. Results can match meaning across languages
without sharing words with the catalog. Exact service-name matches remain
visible even if the model scores them poorly.

All filtered candidates are processed in batches of 100, with one 15-second
search timeout. JSON IDs and score ranges are validated; unknown IDs never
create tools. Missing providers, malformed output, contention and timeouts
return a visibly labelled text-search fallback. Browsing and selecting still
work without an extra embedding service or provider key. Search has its own
provider-client scope and permits one active ranking per provider/model.
Closing or superseding a request cancels its ranking task when the HTTP client
disconnects. The menu discards stale responses.

The semantic implementation is LLM ranking, not an embedding/vector index. This
avoids introducing another mandatory model/provider. Its tradeoff is model
latency and usage per search; very large catalogs may fall back to text search
under the timeout. No model is called merely to open Add.

## Message contract and safety

`GET /api/agent-chat/tools` returns `ToolChoice` rows, search mode and total.
`POST /api/agent-chat/sessions/{id}/messages` accepts optional `tool_choices`, a
list of at most 24 stable IDs. Labels, descriptions and tool objects supplied by
a client never authorize anything. The server resolves IDs against the current
catalog before creating an event, and resolves them again after waiting for
the brain turn lock. Missing or disconnected selections fail explicitly.

The existing SQLite event payload stores validated `tool_choices` receipts.
No SQL schema migration is needed. `ToolChoice` (Pydantic), `toolChoices.ts`, the
user-event reducer and rendered chips share that vocabulary; the contract test
pins both fields and category spellings. Old events without receipts still load.

Validated tools join `TurnOverride.tools_extra`, after the normal heuristic
relevance pruning; the existing stance filter runs last. Only identifiers enter
the per-turn system addendum. The executor still owns risk classification,
blacklist policy and approval. No global provider, permission or tool registry
is modified, and tags do not carry into subsequent turns.

## Verification and limits

`tests/contract/test_chat_tool_choices.py` covers inventory grouping, availability,
semantic matching without word overlap, malformed output, exact-match fallback,
invalid IDs, plan filtering, per-turn isolation, SQLite reopen and Python/TS/i18n
parity. Fresh-database turns are checked with one simulated key for each of
OpenAI, Gemini and OpenRouter; this is not a live-provider installation test.
Existing agent-chat tests cover the approval bridge, routing and attachments.
The frontend tests cover selection/removal, original-logo reuse, category/query
transport, setup navigation, retry and backwards-compatible receipts.

The implementation uses portable Python/React APIs on Windows, macOS and Linux,
including headless servers. Only capabilities actually loaded on the host can
be selected. Native OS tools retain their existing platform limitations. Physical
macOS/Linux runs and a live-provider fresh installation remain external checks.

The API is automatically available through the mounted `agent-chat` CLI group;
`python scripts/ci/check_cli_coverage.py` checks this. There is no separate CLI
state or second tool-selection pipeline.

## Shipped connector snapshot

| Service | Catalog ID | Native tool, when present |
| --- | --- | --- |
| GitHub | `github` | `MCP/channel connector` |
| Vercel | `vercel` | `vercel` |
| Supabase | `supabase` | `MCP/channel connector` |
| Notion | `notion` | `MCP/channel connector` |
| Slack | `slack` | `MCP/channel connector` |
| Linear | `linear` | `MCP/channel connector` |
| Stripe | `stripe` | `MCP/channel connector` |
| Cloudflare | `cloudflare` | `MCP/channel connector` |
| Discord | `discord` | `MCP/channel connector` |
| Telegram | `telegram` | `MCP/channel connector` |
| Asana | `asana` | `MCP/channel connector` |
| Google Drive | `google_drive` | `google_drive` |
| Gmail | `gmail` | `gmail` |
| Google Calendar | `google_calendar` | `google_calendar` |
| Todoist | `todoist` | `MCP/channel connector` |
| ClickUp | `clickup` | `MCP/channel connector` |
| Dropbox | `dropbox` | `MCP/channel connector` |
| Canva | `canva` | `MCP/channel connector` |
| Airtable | `airtable` | `MCP/channel connector` |
| Cal.com | `cal_com` | `MCP/channel connector` |
| Home Assistant | `home_assistant` | `home_assistant` |
| Spotify | `spotify` | `spotify` |
| YouTube Music | `youtube_music` | `youtube_music` |
| Higgsfield | `higgsfield` | `MCP/channel connector` |

## Registered tool entry points

`open-app`, `type-text`, `hotkey`, `click`, `run-shell`, `search-web`, `gmail`, `vercel`, `home_assistant`, `google_calendar`, `google_drive`, `spotify`, `youtube_music`, `screen-snapshot`, `move-mouse`, `switch-window`, `read-visible-ui-state`, `wait-for-ui-state`, `click-element`, `scroll`, `wait-for-element`, `remember`, `dispatch-to-harness`, `computer-use`, `whoami`, `dispatch-to-admin`, `multi-spawn`, `spawn-worker`, `dispatch-with-review`, `cli-tools`, `plugin-tools`, `mcp-tools`, `verify-via-curl`, `verify-localhost`, `start-preview-server`, `inspect-pointer`, `navigate`, `create-artifact`, `app-command`, `awareness-snapshot`, `awareness-recall`, `delegate-to-agent`, `society-status`, `message-agent`, `run-skill`, `wiki-recall`, `wiki-page-read`, `wiki-list`, `wiki-ingest`, `update-profile`, `create-skill`, `describe-app-settings`, `switch-provider`, `manage-mcp-server`, `reveal-key-preview`, `contact-lookup`, `contact-upsert`, `call-contact`.

## Chat folder tools

`Read`, `Write`, `Edit`, `Ls`, `Glob`, `Grep`, `RunCommand`.

## Local verification record

The production build and an isolated component-browser check passed. The browser
check exercised adding Gmail, showing/removing chips, light/dark rendering and
a narrow viewport using fixture catalog data, not a live user account.
The final dedicated contract suite has 17 passing tests. The broader Python run
had 1,029 passes and six unrelated CLI failures: a missing `costs` command-index
entry and five assertions for a missing `local-models assistant` group. The four
required routing/output/hangup/language guards passed in that run. These CLI
failures are not treated as proof of a successful full suite.
