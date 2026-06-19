---
plugin_id: cloudflare
keywords: cloudflare, worker, workers, dns, pages, r2, domain, deployment, deploy, zone, zones, cache, logs, analytics, radar, observability, waf, firewall, route, routes
---
Use cloudflare/* tools to query Workers logs, analytics, DNS zones, and Radar insights.
- The default server targets the read-only observability endpoint; write scopes must be
  granted explicitly on Cloudflare's consent screen.
- Specify zone or account ID when filtering logs or DNS records.
- Read/act directly; report the resource name and key finding afterwards.
- Additional endpoints (Radar, GraphQL, Bindings) are available if those scopes were granted.
