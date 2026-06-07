---
schema_version: "1"
name: plugin-gmail
description: Read and send email from the user's connected Gmail inbox.
category: communication
plugin_id: gmail
intent_verbs: [lies, lese, schick, sende, antworte, zeig, check]  # i18n-allow
intent_objects: [postfach, inbox, gmail, mail, email, e-mail, mails, nachrichten, posteingang]  # i18n-allow
triggers:
  - type: voice
    pattern: "(gmail|postfach|posteingang|meine? mails?|neue mails?)"  # i18n-allow
requires_tools: [gmail]
risk_policy:
  default_tier: ask
---

Use the connected Gmail tools to read and send mail on the user's behalf.

- Search the inbox with a query before acting; read a specific message by id when the user references one.
- Before SENDING, confirm recipient, subject and body with the user (ask-tier — sending mail is consequential).
- Summarize results plainly: sender, subject, date. Omit raw message ids and full headers unless asked.
- Never paste secrets into a reply.
