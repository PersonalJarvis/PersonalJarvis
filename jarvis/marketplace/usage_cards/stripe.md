---
plugin_id: stripe
keywords: stripe, zahlung, zahlung, payment, payments, kunde, kunden, customer, customers, rechnung, rechnungen, invoice, invoices, subscription, abo, abonnement, charge, refund, rückerstattung, balance, guthaben, preis, price
---
Use stripe/* tools to look up customers, payments, invoices, and balance — read-first.
- The hosted MCP server is read-focused; destructive mutations require explicit user intent.
- Always look up the customer by email or ID before acting on their data.
- Summarize: amount, currency, date, and status; omit raw IDs unless asked.
- Read/act directly; state what you retrieved or changed afterwards.
