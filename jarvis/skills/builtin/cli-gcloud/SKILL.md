---
schema_version: "1"
name: cli-gcloud
version: "1.0.0"
description: |
  Drive Google Cloud through the connected gcloud CLI (the cli_gcloud tool) —
  billing/costs, projects, compute, storage, IAM — instead of opening the Cloud
  Console in a browser via Computer-Use (which stalls on the login page).
  Read-first; treat resource-changing commands as consequential.
when_to_use: >-
  Use when the user asks about their Google Cloud — billing/costs, projects,
  VMs, buckets, IAM roles — and gcloud is connected. Reach for the cli_gcloud
  tool, never the browser console.
category: meta
tags: [gcloud, google-cloud, cli, billing]
author: builtin
license: MIT
requires_tools: [cli_gcloud]
risk_policy:
  default_tier: ask
---

# Google Cloud via gcloud (not the browser)

When gcloud is connected, drive Google Cloud through the **`cli_gcloud`** tool —
headless, no browser login. This is the recipe for the common read tasks; it
does NOT replace the risk gate (resource-changing commands still confirm).

## Rules

- **Read-first.** List/describe before changing anything. Treat any command that
  creates, deletes, or modifies a resource as consequential — confirm first.
- **Non-interactive.** gcloud runs with prompts disabled; never wait on a `(y/N)`.
- **Honest on failure.** If a command exits non-zero (e.g. "billing API not
  enabled", "permission denied"), say exactly that — never invent a number.

## Costs / billing

```bash
gcloud billing accounts list --format=json
gcloud billing projects list --billing-account=<ACCOUNT_ID> --format=json
```

Billing detail needs the Cloud Billing API enabled and the right IAM role. If
either is missing the command fails — report the failure honestly, do not guess.

## Projects, compute, storage

```bash
gcloud projects list --format=json
gcloud compute instances list --format=json
gcloud storage buckets list --format=json
```

## IAM

```bash
gcloud projects get-iam-policy <PROJECT_ID> --format=json
```

Summarize the result for the user (account/project, amount + currency + period
for billing); omit raw ids unless asked.
