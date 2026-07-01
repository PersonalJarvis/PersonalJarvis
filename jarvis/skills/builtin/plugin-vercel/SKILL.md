---
schema_version: "1"
name: plugin-vercel
description: Inspect the user's Vercel projects and deployments.
when_to_use: Use when the user mentions Vercel or asks about deployment status, projects, or build results.
category: developer
plugin_id: vercel
intent_verbs: [zeig, lies, prüf, check]  # i18n-allow
intent_objects: [vercel, vercel-deployment, vercel-projekt, deployment, deployments]  # i18n-allow
triggers:
  - type: voice
    pattern: "(vercel|vercel-deployment|deployment|auf vercel)"  # i18n-allow
requires_tools: [vercel]
risk_policy:
  default_tier: monitor
---

Use the connected Vercel tools to inspect the user's projects and deployments — read-only.

- List projects or deployments before reporting; reference them by name.
- Summarize plainly: project name, deployment state (ready/building/error), and the URL.
- This tool does not trigger deployments or change settings; it reports status.
