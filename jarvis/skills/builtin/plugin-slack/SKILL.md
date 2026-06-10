---
schema_version: "1"
name: plugin-slack
description: Read and post messages in the user's Slack workspace and channels.
when_to_use: Use when the user mentions Slack or wants to read or post messages in their Slack workspace.
category: communication
plugin_id: slack
intent_verbs: [schick, sende, poste, zeig, lies, antworte]  # i18n-allow
intent_objects: [slack, slack-channel, slack-kanal, slack-workspace, slack-nachricht]  # i18n-allow
triggers:
  - type: voice
    pattern: "(slack|slack-channel|slack-kanal|auf slack)"  # i18n-allow
requires_tools: [slack]
risk_policy:
  default_tier: ask
---

Use the connected Slack tools to read and post messages in the user's workspace.

- Resolve the target channel or user before posting.
- Confirm message content before sending (ask-tier).
- Summarize plainly: channel, author, gist of the message.
