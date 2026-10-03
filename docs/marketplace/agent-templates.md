# Agent templates — sharing an agent through the marketplace

**Status:** shipped 2026-10-03 ·
**Registry side:** `PersonalJarvis/marketplace` (`scripts/validate.py` →
`validate_agent`, `scripts/intake.py`, workflow `intake.yml`) ·
**App side:** `jarvis/society/agent_template.py`

A person who built a good agent can hand it to everybody else: its job line,
its standing instructions, its look and the tools it reaches for first. What
travels is the agent's **design**, never its operation — no account, no
model choice, no chats, no memory, no files, no permissions that would run
anything without asking on somebody else's machine.

## The flow

```
Agent card ─ Share ─► Share sheet ──────────────► Publish (signed in with GitHub)
                       │ template built + scrubbed      │
                       │ by the server                   ▼
                       │                         issue on PersonalJarvis/marketplace
                       │ "Ask <agent>"                   │  (opened AS the user)
                       └► chat message → agent           ▼
                          society_share_template    intake.yml: publisher = issue author,
                          writes the public version validate.py, commit, publish.yml
                                                          │
                                                          ▼
                                         index.json "agents" ─► Marketplace → Agents
                                                          │      Install / jarvis marketplace install
                                                          ▼
                                                 a NEW agent on the installer's own model
```

1. **Share** (agent card, Options rail; never on the lead or sample rows)
   opens the sheet. The server builds the template from the roster row
   (`GET /api/society/agents/{id}/template`); the sheet shows it as a code
   block to copy or export (`<name>.agent.json`) beside the public version:
   the store-card summary, the marketplace name, version and tags.
2. **Ask &lt;agent&gt;** sends the agent a chat message asking it to write its
   own public version — a summary for strangers and its instructions with
   everything personal taken out. The agent saves it with the
   `society_share_template` tool (`jarvis/society/share_tool.py`, `safe`
   tier: it writes a local draft only). The sheet polls until the draft
   changes. The agent is never the privacy boundary — what it writes goes
   through the same scrubber as everything else.
3. **Publish** needs the GitHub sign-in (device flow on the marketplace
   GitHub App, the same identity the Publish studio uses). The app opens an
   issue on the registry as that user; the registry publishes it within a
   minute or two and the sheet watches the feed until the version is live.
4. **Install** — from the marketplace card, from
   `jarvis marketplace install <name>`, or by importing an exported file —
   creates a new agent. A name already on the team gets a number
   ("Release Scribe 2"); the roster would otherwise adopt the existing agent.

## What a template carries — and what it never does

The template is an allowlist (`TEMPLATE_KEYS` in `agent_template.py`, the
same list as `AGENT_KEYS` in the registry validator — change both):

| Field | Meaning |
|---|---|
| `schema` | `1` |
| `name`, `title` | roster name (1–40 chars, never "Jarvis") and job line |
| `instructions` | the standing instructions (the roster `description`), ≤ 20 000 bytes |
| `tier` | `specialist` or `orchestrator` — never `lead` |
| `effort` | a short word or empty |
| `focus`, `grant_mode`, `grants`, `denies`, `skills` | which tools it reaches for first / may use |
| `require_approval` | capability ids that always ask — can only ADD confirmations |
| `knowledge_scope` | `shared` or `own` |
| `avatar` | the figure recipe: catalog ids and `#rrggbb` colours only |

Refused by name, in the app and in the registry, because each would either
point at the author's machine or let a stranger's template decide what runs
without asking on the installer's machine: `account_id`, `provider`, `model`,
`workspace_dir`, `wiki_namespace`, `computer_id`, `parent_agent_id`,
`permission_ceiling`, `approval_mode`, `approval_rules` / `always_allow`,
`daily_budget_usd`, `max_concurrent_runs`, `browser_mode`,
`browser_allowed_domains`, and an imported figure file (`avatar.model`).

This is stricter than the machine-to-machine ecosystem bundle
(`jarvis/mcp/agents/portable.py`), which moves a person's OWN team to their
own other computer and therefore keeps model choices and budgets.

## Scrubbing

`scrub_text` replaces, in the instructions, the job line and the summary:

- credentials — the registry's secret patterns plus generic
  `password: …` / `api_key = …` shapes and secret URL parameters
  (`?token=…`, `&key=…`);
- e-mail addresses (except `example.com/.org/.net`);
- international phone numbers (a leading `+`; bare digit runs are dates and
  ids far more often);
- home folders (`C:\Users\…`, `/Users/…`, `/home/…`);
- private network addresses (10/8, 172.16/12, 192.168/16).

Each replacement is listed on the sheet with a hint that names it to its
author without repeating it (`ma…om`). The registry independently refuses a
credential anywhere in the file and a home folder in the instructions.

## Publishing without a server

The storefront that used to receive submissions was retired, so publishing
now needs no server of ours at all:

- The marketplace GitHub App (`publish_github_client_id`) signs the user in
  with the **device flow** — no client secret in the binary.
- Its user token opens an **issue** on `publish_registry_repo`
  (default `PersonalJarvis/marketplace`). A GitHub App user token can do that
  on a public repository where the App is installed **with the Issues
  permission** — the same mechanism giscus and utterances use for comments.
- The registry's `intake.yml` runs on `issues` events from the default
  branch only, reads the body as data (never executes it, never interpolates
  it into a shell), **replaces `publisher` and `publisher_id` with the issue
  author**, runs `validate.py` with the pre-change tree as base (ownership by
  numeric account id, version must increase, kind may not change), commits
  `submissions/<name>.json` and starts `publish.yml`. Red checks are answered
  on the issue with the reasons; editing the issue re-runs them.

The same door serves skills and plugins: with no `publish_endpoint`
configured, the Publish studio files its submissions the same way, which
brought in-app publishing back after the storefront's retirement. A fork can
point `publish_registry_repo` at its own registry, or set `publish_endpoint`
to a hosted endpoint of its own.

**If GitHub refuses the issue** (App without the Issues permission, issues
turned off), the sheet says so in one sentence and offers *Copy and open
GitHub*: the template goes to the clipboard and the registry's
"Publish to the marketplace" issue form opens in the browser — one paste,
same checks.

## Installing safely

`create_fields` turns a validated template into roster fields; the install
route (`POST /api/society/templates/install`, also used by the marketplace's
install-by-name) adds:

- the model is the installer's own (the create route inherits their last
  chat seat); nothing of the publisher's accounts travels;
- `approval_mode = ask` wherever the resolved runner can hold an approval
  prompt, so a stranger's agent asks before it acts until the person changes
  that; runners that cannot fall back to the app's default;
- `always_allow` is always empty; `require_approval` only adds prompts;
- budget, concurrency, ceiling and browser settings are the app's defaults.

The marketplace card shows, before the Install button: the instructions in
full, the tools it reaches for first, what it always asks before, and that it
runs on the installer's own model.

## Pieces

| Piece | Where |
|---|---|
| Build, scrub, validate, install fields, share draft on disk | `jarvis/society/agent_template.py` |
| Agent tool `society_share_template` | `jarvis/society/share_tool.py` (registered in `surface.society_tools`) |
| REST `GET/PUT /api/society/agents/{id}/template`, `POST …/template/publish`, `POST /api/society/templates/install` | `jarvis/ui/web/society_routes.py` |
| Issue intake submit, `registry_repo()`, agent drafts | `jarvis/marketplace/publish.py` |
| Feed model `CommunityAgentEntry`, `index.agents` | `jarvis/marketplace/community_source.py` |
| Browse payload `agents`, install by name | `jarvis/ui/web/marketplace_routes.py` |
| CLI `jarvis marketplace install/browse` | `jarvis/cli_ctl/commands/marketplace.py` |
| Share sheet | `frontend/src/components/society/card/ShareAgentDialog.tsx` |
| Agents shelf, drawer, import | `frontend/src/views/MarketplaceView.tsx` |
| Client | `frontend/src/lib/agentShare.ts` |

Share drafts live at `<data>/society/<agent_id>/share-template.json`; they
hold only the public-version edits and the last published version.
