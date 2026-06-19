---
schema_version: "1"
name: plugin-discord
description: Read and post messages in the user's Discord server.
when_to_use: Use when the user mentions Discord or wants to read or post messages in their Discord server.
category: communication
plugin_id: discord
intent_verbs: [schick, sende, poste, zeig, lies, antworte]  # i18n-allow
intent_objects: [discord, discord-server, discord-channel, discord-kanal, discord-nachricht, guild]  # i18n-allow
triggers:
  - type: voice
    pattern: "(discord|discord-server|discord-channel|auf discord)"  # i18n-allow
requires_tools: [discord]
risk_policy:
  default_tier: ask
---

Use the connected Discord tools to read and post messages in the user's server.

- Resolve the target channel before posting.
- Confirm message content before sending (ask-tier).
- Summarize plainly: channel, author, gist of the message.
