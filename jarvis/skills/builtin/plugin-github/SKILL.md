---
schema_version: "1"
name: plugin-github
description: Work with the user's GitHub repositories, issues, and pull requests.
category: developer
plugin_id: github
intent_verbs: [zeig, lies, erstell, öffne, schließ, merge, review, kommentier]  # i18n-allow
intent_objects: [github, github-issue, repo, repository, pull, pullrequest, pr, branch, commit, workflow]  # i18n-allow
triggers:
  - type: voice
    pattern: "(github|pull request|pull-request|\\bpr\\b|repo|repository)"  # i18n-allow
requires_tools: [github]
risk_policy:
  default_tier: ask
---

Use the connected GitHub tools to inspect and act on the user's repos, issues, and pull requests.

- Read-first: list/search issues or PRs before acting; reference items by number.
- Confirm before destructive/consequential actions (merging, closing).
- Summarize plainly: title, number, state, author. Keep raw ids out unless asked.
