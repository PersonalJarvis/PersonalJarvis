---
schema_version: "1"
name: plugin-stripe
description: Look up Stripe customers, payments, invoices, and account balance.
when_to_use: Use when the user mentions Stripe, payments, invoices, customers, or their account balance.
category: developer
plugin_id: stripe
intent_verbs: [zeig, lies, erstatt, prüf, such]  # i18n-allow
intent_objects: [stripe, zahlung, zahlungen, payment, payments, kunde, kunden, customer, customers, rechnung, rechnungen, invoice, invoices, abo, abonnement, subscription, guthaben, balance, charge, refund]  # i18n-allow
triggers:
  # Word boundaries are load-bearing: without them "kunde" fired inside
  # "SeKUNDE", so every spoken "warte eine Sekunde" pulled a PAYMENT skill into
  # the turn. Same defect class as the calendar skill's "termin" in "Terminals"
  # (BUG-121), and the more dangerous one, because these tools move money.
  - type: voice
    pattern: "\\b(stripe|zahlung(?:en)?|payment(?:s)?|rechnung(?:en)?|invoice(?:s)?|guthaben|kunde(?:n)?)\\b"  # i18n-allow
requires_tools: [stripe]
risk_policy:
  default_tier: ask
---

Use the connected Stripe tools to look up customers, payments, invoices, and balance — read-first.

- Always look up the customer by email or id before acting on their data.
- Summarize amount, currency, date, and status; omit raw ids unless asked.
- The hosted Stripe MCP is read-focused; treat refunds/mutations as consequential and confirm first.
