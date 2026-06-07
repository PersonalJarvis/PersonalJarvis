---
schema_version: "1"
name: plugin-cloudflare
description: Manage the user's Cloudflare DNS records, zones, and Workers.
category: developer
plugin_id: cloudflare
intent_verbs: [zeig, lies, erstell, konfigurier, aktualisier]  # i18n-allow
intent_objects: [cloudflare, cloudflare-dns, cloudflare-zone, cloudflare-worker, cloudflare-record]  # i18n-allow
triggers:
  - type: voice
    pattern: "(cloudflare|cloudflare-dns|cloudflare-zone|in cloudflare)"  # i18n-allow
requires_tools: [cloudflare]
risk_policy:
  default_tier: ask
---

Use the connected Cloudflare tools to manage the user's DNS, zones, and Workers.

- List zones or records before changing them; reference by name.
- Treat DNS and config changes as consequential; confirm first.
- Summarize plainly: zone, record type, value, status.
