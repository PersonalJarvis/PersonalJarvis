# Validator parity — the upload endpoint vs. the registry CI

**Status:** FINDINGS — measured 2026-08-14 ·
**Authority:** `scripts/validate.py` in `PersonalJarvis/marketplace` ·
**Compared against:** `functions/_lib/validate.ts` in the storefront ·
**Related:** [github-signin-implementation.md](github-signin-implementation.md) §4

## Why this file exists

The submit endpoint validates a submission, then opens a pull request that
CI validates again. When the endpoint is *stricter*, an author sees a clear
field error and fixes it — no harm. When the endpoint is *looser*, the
author gets `201 {prUrl}` and a success message, and the pull request then
fails CI and sits open forever. Nobody is watching that pull request: the
author thinks they published, and the maintainer sees a queue of red
strangers.

So the direction of a divergence decides whether it matters. Everything
below is a case where the endpoint accepts what CI rejects.

This is **not** a security hole. `validate.ts` says so itself, correctly:
CI remains the final authority and nothing bad merges. It is a usability
hole that produces orphaned pull requests — with one partial exception,
noted at the end.

## Divergences that produce an orphaned pull request

| # | Rule CI enforces | Where | Endpoint today |
|---|---|---|---|
| 1 | Reserved plugin ids (25 names incl. `github`, `slack`, `stripe`) | `RESERVED_PLUGIN_IDS` | not checked |
| 2 | Reserved skill names (32 incl. every `plugin-*`) | `RESERVED_SKILL_NAMES` | not checked |
| 3 | `skill_md` frontmatter must declare the same `name:` as the submission | `validate_skill` | not checked |
| 4 | `plugin_json.$schema` must equal the Agent Plugins v1.0.0 URL exactly | `validate_plugin` | not checked |
| 5 | `plugin_json.name` must equal the submission name | `validate_plugin` | not checked |
| 6 | `plugin_json.description` is **required** (not just length-capped) | `validate_plugin` | length checked only when present |
| 7 | `extensions["io.github.personaljarvis"]` is required | `validate_plugin` | not checked |
| 8 | `native_tool` is refused (repo-contributed code, not publishable) | `validate_plugin` | not checked |
| 9 | `auth.mode` must be one of the five known modes | `AUTH_MODES` | not checked |
| 10 | `mcp_auth_header_template` needs a `$plugin_…` placeholder and may embed no literal credential | `validate_plugin` | not checked |
| 11 | **No `http://` URL anywhere in the document**, recursively | `reject_http_urls` | only the MCP server url is checked |
| 12 | `headers` on a streamable-http server are refused | `validate_mcp_json` | not checked |
| 13 | Every `env` value must be a `$plugin_…` placeholder | `validate_mcp_json` | not checked |
| 14 | **No** argument may end in `@latest` | `validate_mcp_json` | passes if *any* argument looks pinned, so `npx foo@1.2.0 bar@latest` is accepted |
| 15 | `sse` transport is refused by name | `validate_mcp_json` | accepted — an sse server has a `url`, so it takes the hosted branch |
| 16 | Only `mcpServers` is read | `validate_mcp_json` | also accepts `servers` as an alias, which CI then cannot find |
| 17 | Size is measured on the **final file**: pretty-printed, `publisher` and `publisher_id` included | `validate_file` | measured on compact `JSON.stringify` of the value *before* the publisher fields are added |

Number 17 is the quiet one. Indentation on a nested manifest adds real
weight, so a submission that measures just under 128 KB at the endpoint can
cross the limit once written to disk — and it only bites the largest
submissions, which are also the ones an author least wants to redo.

Numbers 1 and 2 are the likeliest to be hit in practice: `github`,
`notion`, `slack` and `stripe` are exactly the names a newcomer reaches for.

## The one with a security edge

`SECRET_PATTERNS` has nine entries in CI and five at the endpoint. Missing:
`github_pat_…`, Google `AIza…` keys, JWTs, and `sk-` keys containing `-` or
`_`. A submission carrying one of those is accepted, and the App **commits
it to a branch** before CI ever runs. The pull request will fail and never
merge, but the credential is in the repository's history from that moment.

The registry repo is private today, which contains the blast radius. It is
scheduled to be reopened once the login path is green (spec §8 step 5), so
this should close before that happens.

## The fix that stops it recurring

`rules.json` is now generated from `validate.py` and published next to the
feed:

```
https://personaljarvis.github.io/marketplace/rules.json
```

It carries the data-driven half — limits, patterns, reserved names, the
launcher and auth allowlists, all nine secret patterns, the plugin schema
URL — with patterns written so Python `re` and ECMAScript both accept them
verbatim. The registry's validate workflow fails if the file drifts from
the validator, so it cannot go stale unnoticed.

Reading that file closes 1, 2, 4, 9 and the secret-pattern gap outright,
and gives 3, 5–8, 10–16 their constants. Those remaining ones are logic
rather than data and still need writing once against the table above.

Number 17 needs no shared data at all: measure the bytes of the exact JSON
the App is about to commit — pretty-printed, publisher fields included —
rather than the payload as received.
