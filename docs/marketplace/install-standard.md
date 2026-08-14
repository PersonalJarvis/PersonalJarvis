# The marketplace install standard

Status: shipped 2026-08-14. Consumer half of the store; the publishing half
lives in [publishing-plan.md](publishing-plan.md).

Every installable community entry — plugin or skill — is offered the same
three ways on its detail page, the pattern comparable stores (ClawHub,
Smithery, LobeHub) converged on, adapted from the npm world to a Python app:

| Surface  | Line                                                          | Who types it |
| -------- | ------------------------------------------------------------- | ------------ |
| `cli`    | `jarvis marketplace install <name>`                            | a terminal with the CLI installed |
| `runner` | `uvx --from personal-jarvis jarvis marketplace install <name>` | a terminal WITHOUT the CLI — `uvx` resolves the published package into a throwaway environment first |
| `prompt` | `Install the "<name>" plugin from the community marketplace.`  | pasted at the assistant, which runs the curated CLI command through its CLI tool |

## The one-source rule

The three strings are computed in **one** place —
`jarvis/marketplace/install_standard.py` — and flow outward verbatim:

1. `/api/marketplace/community` embeds an `install` block per entry
   (`marketplace_routes.py::_community_payload`).
2. The store's detail sheets render that block verbatim
   (`PluginsCommunity.tsx::InstallStandard`) — the frontend never derives
   command strings itself.
3. The curated command (`jarvis/cli_ctl/commands/marketplace.py`) is the only
   consumer-side implementation. Renaming or reshaping it requires the same
   change in `install_standard.py`, or the store advertises a command that
   does not exist — the one bug a store is judged on.

Entries that cannot install get **no block** instead of a failing command:
skills without a `raw_url` (manual installs) and plugins whose name collides
with a built-in id.

## Resolution semantics of `jarvis marketplace install <name>`

One name, no flags, kind inferred — the CLI resolves against the community
index: plugin first, then skill (a future registry enforces one namespace, so
the tiebreak is transitional). `--refresh` re-fetches the index first.
Before the confirm gate the command prints the SAME facts the store's consent
dialog shows — publisher, hosted URL or local argv — because community
content is unreviewed; `--yes` is the CLI's version of clicking Install.
`jarvis marketplace uninstall <name>` mirrors the store's Remove buttons.

## Why `uvx` is the npx equivalent

`npx` runs an npm package's binary without installing it; `uvx` does exactly
that for a PyPI package's entry point. The runner line therefore works on any
OS with `uv` present and no prior install — it fetches the published
`personal-jarvis` package and executes the same curated command against the
locally running app. It deliberately does NOT bypass the app: installs always
go through the running instance's REST routes, so consent, conflict checks
and rollback behave identically on every surface.

## Why the prompt works end to end

The assistant has no dedicated install tool, on purpose: its CLI tool runs
`jarvis marketplace install <name>`, which carries the confirm gate and the
danger metadata every curated mutation has. One command, one safety model,
three surfaces.
