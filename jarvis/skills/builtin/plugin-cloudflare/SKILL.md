---
schema_version: "1"
name: plugin-cloudflare
description: Manage the user's Cloudflare DNS records, zones, and Workers.
when_to_use: Use when the user mentions Cloudflare or wants to inspect or change DNS records, zones, or Workers.
category: developer
plugin_id: cloudflare
intent_verbs: [zeig, lies, erstell, konfigurier, aktualisier]
intent_objects: [cloudflare, cloudflare-dns, cloudflare-zone, cloudflare-worker, cloudflare-record]
triggers:
  - type: voice
    pattern: "(cloudflare|cloudflare-dns|cloudflare-zone|in cloudflare)"
requires_tools: [cloudflare]
risk_policy:
  default_tier: ask
---

Use the connected Cloudflare tools to manage the user's DNS, zones, and Workers.

- List zones or records before changing them; reference by name.
- Treat DNS and config changes as consequential; confirm first.
- Summarize plainly: zone, record type, value, status.
