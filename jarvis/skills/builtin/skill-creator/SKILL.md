---
schema_version: "1"
name: skill-creator
version: "1.2.0"
description: |
  Creates a new Jarvis skill from the user's description — the assistant writes
  the whole skill (name, trigger phrase, schedule, steps, spoken answer format)
  and files it as a draft. Use when the user wants to build a new skill,
  routine or automation ("erstell mir einen Skill, der …" / "create a skill
  that …" / "mach aus diesem Workflow einen Skill").
when_to_use: >-
  Use when the user asks to create, build, or set up a new skill, routine, or
  recurring automation — including when the request names other services that
  are the CONTENT of the new skill, not commands to run now.
category: meta
tags: [meta, authoring, developer, skill-creator]
# Matcher vocabulary (2026-08-12): authoring requests name the mechanism, so
# "skill" plus the automation nouns must score here — before this list,
# "bau mir einen skill der abends das licht dimmt" lost to the DOMAIN skill
# (home assistant) because only the domain words carried weight. No
# intent_verbs on purpose (plugin_coupling needs verbs AND objects to
# register a capability; this feeds only the matcher).
intent_objects: [skill, skills, automatisierung, automation, workflow, ablauf, faehigkeit, routine]  # i18n-allow: speech-input vocabulary
author: builtin (Anthropic skill-creator guide kept in references/)
license: MIT
# 2026-08-18: the deterministic authoring resolver
# (jarvis/skills/authoring_request.py) now decides "the user wants a NEW skill"
# BEFORE any brand trigger runs, so a request that mentions YouTube Music or
# Gmail inside the description can no longer be captured by that connector's
# skill. This regex stays as the offline-eval / match-test evidence and as the
# second line of defence, and it now accepts every spoken conjugation after the
# noun ("… einen neuen Skill erstellst") — the 2026-08-18 17:51 turn used
# exactly that form and missed the old infinitive-only alternative.
# Verb heads are written with a character class ([e]rstell, b[a]u, …) ON
# PURPOSE: mine_pattern_literals flushes literal runs at "[", so the generic
# creation verbs never enter the relevance index as trigger-weight vocabulary.
# Without the break, "schreib" scored for THIS skill and stole "schreib eine
# mail an das team" from plugin-gmail (offline golden eval, 2026-08-12). The
# skill-specific noun ("skill") is the only strong literal this pattern feeds.
triggers:
  - type: voice
    pattern: "([e]rstell\\w*|b[a]u\\w*|m[a]ch\\w*|schr[e]ib\\w*|gener[i]er\\w*|[a]nleg\\w*)\\s+(mir\\s+)?(bitte\\s+)?(doch\\s+)?(mal\\s+)?einen?\\s+(neuen?\\s+)?skill|einen?\\s+(neuen?\\s+)?skill\\s+(\\w+\\s+){0,3}([e]rstell\\w*|[b]au\\w*|[a]nleg\\w*|[s]chreib\\w*|[g]enerier\\w*)|aus\\s+(diesem|dem|meinem)\\s+\\w+\\s+einen\\s+skill|(cre[a]te|b[u]ild|m[a]ke|s[e]t\\s+up)\\s+(me\\s+)?an?\\s+(new\\s+)?skill|t[u]rn\\s+(this|that|it)\\s+into\\s+a\\s+skill|(cre[a]\\w*|h[a]z|constr[u]\\w*)\\s+(me\\s+)?una?\\s+(nuevo\\s+|nueva\\s+)?skill"
    language: [de, en, es]
requires_tools: []
risk_policy:
  default_tier: monitor
config:
  target_dir: user
  default_category: general
token_budget_estimate: 1500
---

# Skill Creator

The user wants a NEW skill. Write it for them — do not interview them.

## Steps

1. Restate everything the user asked for as one complete description: what the
   skill does, in which order, at what time or on which spoken phrase it should
   fire, which services it touches (mail, calendar, tickets, music, smart home,
   …), and every preference or example they gave (counts, genres, "always
   something new"). Services named inside the request are the skill's content —
   never act on them now (do not play the song, do not read the mail).
2. Call the `create-skill` tool ONCE with that description as `intent`, the
   name the user gave (or an empty string), the spoken trigger phrase if they
   named one, and the schedule as a 5-field cron when they named a time
   ("jeden Morgen um 6" → `0 6 * * *`). The assistant writes the whole skill
   itself — steps per connected tool, trigger, spoken answer format — and files
   it as a draft.
3. If the result says no model could write it, say so plainly and offer to try
   again in a moment. Do not fall back to a worker and do not paste the request
   into a file yourself.
4. If the user asks to change an EXISTING skill, tell them that editing lives in
   the Skills view (this card only creates); name the skill they mean.

## Answer format

One or two spoken sentences in the user's language: the skill's name, what it
does and when it fires, and that it is waiting as a draft in the Skills view
until they switch it on. Never read the generated instructions aloud.

## Reference

Anthropic's full Skill Creator guide (intent → draft → test → iterate) is kept
in `references/anthropic-skill-creator.md`; the Jarvis frontmatter contract is
in `references/jarvis-schema.md`.
