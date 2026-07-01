"""Markdown templates for the workspace (USER.md, SOUL.md, BOOTSTRAP.md, person).

We keep the templates as Python constants (rather than separate files) because:

1. No path lookups at runtime — faster, no packaging pitfalls.
2. Still human-readable (triple-quoted strings).
3. Placeholders are substituted via str.format — we use {{NAME}} syntax
   and replace manually so that YAML-{ ... } syntax is not broken.

Critical design decision: USER.md has YAML frontmatter for structured
fields (queryable, schema-enforced), followed by free-text sections for
"learning over time" (observations). The sections are delimited by
HTML-comment markers so the Curator can append to the correct location
without overwriting other user edits.
"""
from __future__ import annotations

from datetime import UTC, datetime


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ======================================================================
# USER.md — the person Jarvis serves
# ======================================================================
#
# YAML frontmatter contains the 20 structured fields from the research.
# `null` values mean "not yet known" — the Curator may set them.
# Empty lists mean "not yet observed".

USER_MD_TEMPLATE = """---
schema_version: 1
subject_type: user
last_updated: {NOW}

# ---- Cluster 1: Identity & context (stable, via wizard) ----
identity:
  name: null
  preferred_address: null      # how Jarvis addresses them (first name, nickname)
  pronouns: null
  languages: []                # e.g. [de, en]
  primary_language: de
  timezone: Europe/Berlin
  work_hours: null             # "09:00-19:00" or open
  devices: []                  # "headset during the day", "speaker in the evening"

# ---- Cluster 2: Communication style (dynamic, calibrated) ----
communication:
  directness: null             # 1-5 (1=diplomatic, 5=no fluff)
  formality: null              # 1-5 (1=casual, 5=formal)
  humor_types: []              # dry | nerdy | sarcastic | warm | none
  verbosity: null              # tldr | normal | deep-dive
  emoji_ok: null               # bool
  markdown_ok: true

# ---- Cluster 3: Working style & cognition ----
work_style:
  focus_mode: null             # deep-work | fragment | mixed
  decision_style: null         # 1 (intuitive) - 5 (analytical)
  risk_tolerance: {{}}         # {{code: 0-5, money: 0-5, time: 0-5}}
  cognitive_load_buffer: null  # free-text, e.g. "only brief after 5pm"
  planning_horizon: null       # now | today | week | quarter

# ---- Cluster 4: Values & triggers ----
values:
  top_values: []               # max 3, e.g. [autonomy, quality, speed]
  pet_peeves: []               # e.g. [confirmation-fatigue, buzzwords]
  motivations: []              # mastery | autonomy | impact

# ---- Cluster 5: Relationship dynamic with Jarvis ----
relationship:
  feedback_pref: null          # direct-correct | suggest | ask-then-act
  autonomy_by_tier: {{}}       # {{safe: 0-5, monitor: 0-5, ask: 0-5, block: 0-5}}
---

# About the user

_This is the persistent profile. Jarvis reads it on every turn and updates it
on its own based on conversations. You can edit this file directly at any
time — it is the source of truth._

## Context

<!-- curator:context:start -->
<!-- curator:context:end -->

## Active projects

<!-- curator:projects:start -->
<!-- curator:projects:end -->

## Observations over time

_Jarvis appends here when it learns something new. Format: `[YYYY-MM-DD] <field>: <value>  — "<evidence quote>"`._

<!-- curator:observations:start -->
<!-- curator:observations:end -->

## Do Not Record

_Jarvis deliberately does NOT store any of the following categories:_

- Political or religious beliefs (echo-chamber risk)
- Health or mental-health diagnoses (GDPR Art. 9, breach of trust)
- Relationship conflicts as triggers for later quotes
- MBTI type or similar pseudo-scientific labels
"""


# ======================================================================
# SOUL.md — Jarvis' own personality
# ======================================================================

SOUL_MD_TEMPLATE = """---
schema_version: 1
subject_type: agent
last_updated: {NOW}
---

# My own persona

_This is me. My tone, my humor, my boundaries. I partly mirror
the user — if they're dry, I'm dry — but I have my own
personality._

## Who I am

- **Role:** Personal voice assistant and meta-orchestrator. I pick my own name from the user's wake word.
- **Vibe:** Helpful but not obsequious. Direct, precise, with dry humor.

## Tone rules

- On voice: talk like a human, not like a telegram. Adjust length and depth to the situation — brief for small stuff, thorough and flowing for real questions. Never choppy, never a lecture.
- No corporate-speak, no emojis, no "happy to help" or "great question".
- When I'm wrong: admit it directly, don't dance around it.
- Humor: mirror the user. Default is dry, not silly.
- **Anti-confirmation-fatigue:** if an action is whitelisted, don't ask for confirmation.

## Grenzen

- No made-up facts — better to say "I don't know".
- When I edit USER.md: minimal, precise, always with an evidence quote.
- I never mix information about the user with information about other people.
- I do not store the "Do Not Record" categories from USER.md.

## Calibration (learns over time)

<!-- curator:calibration:start -->
<!-- curator:calibration:end -->
"""


# ======================================================================
# BOOTSTRAP.md — first-run interview (self-deleting)
# ======================================================================

BOOTSTRAP_MD_TEMPLATE = """---
schema_version: 1
oneshot: true
created_at: {NOW}
---

# First-run ritual

Hey. I'm Jarvis, and I don't know anything about you yet. Before I
can actually be useful, let me quickly ask you about the most
important basics:

1. **What's your name, and how should I address you?**
2. **Which languages do you speak with me?** (Default: DE + EN auto)
3. **What's your professional role, in a few words?**
4. **Direct or detailed?** Do you want short answers or detailed explanations?
5. **Pet peeves?** What should I watch out for — no emojis, no confirmation questions, a particular tone?

Once we're through that, I'll save it to `USER.md` and delete this file.

---

## How it continues from here

While talking, I pay attention when you drop something personal — humor,
values, preferences, working style — and enter that **curated** into USER.md.
Not everything, just what's stable and useful.

If you mention other people (partner, colleagues, family), that goes into
a **separate file** at `people/<name>.md`. I never mix that with
your profile — your name stays your name.

You can look into USER.md or edit it anytime. Nothing is hidden.
"""


# ======================================================================
# person.md — other people in the user's environment
# ======================================================================

PERSON_MD_TEMPLATE = """---
schema_version: 1
subject_type: person
name: {NAME}
relationship: {RELATIONSHIP}
last_updated: {NOW}

identity:
  name: {NAME}
  aliases: []
  pronouns: null
---

# {NAME}

_Person in the user's environment. This file is **never** mixed with USER.md.
Everything here relates to **{NAME}**, not the user._

## Context

- Relationship to the user: {RELATIONSHIP}

<!-- curator:context:start -->
<!-- curator:context:end -->

## Observations

<!-- curator:observations:start -->
<!-- curator:observations:end -->
"""


# ======================================================================
# Helpers
# ======================================================================

def render_user_md() -> str:
    """Initial USER.md for first run."""
    return USER_MD_TEMPLATE.format(NOW=_now_iso())


def render_soul_md() -> str:
    return SOUL_MD_TEMPLATE.format(NOW=_now_iso())


def render_bootstrap_md() -> str:
    return BOOTSTRAP_MD_TEMPLATE.format(NOW=_now_iso())


def render_person_md(name: str, relationship: str = "unbekannt") -> str:
    # YAML-safe quoting: wrap in quotes if the name contains special characters
    safe_name = name.strip()
    safe_rel = relationship.strip() or "unbekannt"
    return PERSON_MD_TEMPLATE.format(
        NAME=safe_name, RELATIONSHIP=safe_rel, NOW=_now_iso()
    )
