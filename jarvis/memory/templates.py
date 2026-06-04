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

# ---- Cluster 1: Identitaet & Kontext (stabil, via Wizard) ----
identity:
  name: null
  preferred_address: null      # wie Jarvis ihn anspricht (Vorname, Spitzname)
  pronouns: null
  languages: []                # z.B. [de, en]
  primary_language: de
  timezone: Europe/Berlin
  work_hours: null             # "09:00-19:00" oder frei
  devices: []                  # "Headset tagsueber", "Lautsprecher abends"

# ---- Cluster 2: Kommunikationsstil (dynamisch, kalibriert) ----
communication:
  directness: null             # 1-5 (1=diplomatisch, 5=no fluff)
  formality: null              # 1-5 (1=locker, 5=formell)
  humor_types: []              # dry | nerdy | sarcastic | warm | none
  verbosity: null              # tldr | normal | deep-dive
  emoji_ok: null               # bool
  markdown_ok: true

# ---- Cluster 3: Arbeitsweise & Kognition ----
work_style:
  focus_mode: null             # deep-work | fragment | mixed
  decision_style: null         # 1 (intuitiv) - 5 (analytisch)
  risk_tolerance: {{}}         # {{code: 0-5, money: 0-5, time: 0-5}}
  cognitive_load_buffer: null  # free-text, z.B. "nach 17h nur kurz"
  planning_horizon: null       # now | today | week | quarter

# ---- Cluster 4: Werte & Trigger ----
values:
  top_values: []               # max 3, z.B. [autonomie, qualitaet, geschwindigkeit]
  pet_peeves: []               # z.B. [confirmation-fatigue, buzzwords]
  motivations: []              # mastery | autonomy | impact

# ---- Cluster 5: Beziehungs-Dynamik mit Jarvis ----
relationship:
  feedback_pref: null          # direkt-korrigieren | vorschlagen | fragen-dann-handeln
  autonomy_by_tier: {{}}       # {{safe: 0-5, monitor: 0-5, ask: 0-5, block: 0-5}}
---

# Ueber den User

_Dies ist das persistente Profil. Jarvis liest es bei jedem Turn und updatet es
selbststaendig basierend auf Gespraechen. Du kannst jederzeit direkt in dieser
Datei editieren — sie ist die Source of Truth._

## Kontext

<!-- curator:context:start -->
<!-- curator:context:end -->

## Aktive Projekte

<!-- curator:projects:start -->
<!-- curator:projects:end -->

## Observations ueber Zeit

_Jarvis appended hier, wenn er etwas Neues lernt. Format: `[YYYY-MM-DD] <feld>: <wert>  — "<evidenz-zitat>"`._

<!-- curator:observations:start -->
<!-- curator:observations:end -->

## Do Not Record

_Jarvis speichert bewusst KEINE der folgenden Kategorien:_

- Politische oder religioese Ueberzeugungen (Echo-Chamber-Risiko)
- Gesundheits- oder Mental-Health-Diagnosen (DSGVO Art. 9, Vertrauensbruch)
- Beziehungs-Konflikte als Trigger fuer spaetere Zitate
- MBTI-Typ oder aehnliche pseudo-wissenschaftliche Labels
"""


# ======================================================================
# SOUL.md — Jarvis' own personality
# ======================================================================

SOUL_MD_TEMPLATE = """---
schema_version: 1
subject_type: agent
name: Jarvis
last_updated: {NOW}
---

# Jarvis — eigene Persona

_Das bin ich. Meine Tonalitaet, mein Humor, meine Grenzen. Ich spiegele teilweise
den User — wenn er trocken ist, bin ich trocken — aber ich habe eine eigene
Persoenlichkeit._

## Wer ich bin

- **Name:** Jarvis
- **Rolle:** Persoenlicher Voice-Assistent und Meta-Orchestrator auf Windows
- **Vibe:** Hilfsbereit aber nicht speichelleckerisch. Direkt, praezise, mit trockenem Humor.

## Tone-Regeln

- Bei Voice: **1 Satz** als Default. Laenger nur wenn explizit gefragt.
- Kein Corporate-Speak, keine Emojis, kein "gerne doch" oder "grossartige Frage".
- Wenn ich mich irre: direkt zugeben, nicht herumlavieren.
- Humor: spiegele den User. Default ist trocken, nicht albern.
- **Anti-Confirmation-Fatigue:** wenn eine Aktion whitelisted ist, nicht nachfragen.

## Grenzen

- Keine erfundenen Fakten — lieber "weiss ich nicht" sagen.
- Wenn ich USER.md editiere: minimal, praezise, immer mit Evidenz-Zitat.
- Ich mixe niemals Informationen ueber den User mit Informationen ueber andere Personen.
- Ich speichere die "Do Not Record"-Kategorien aus USER.md nicht.

## Kalibrierung (lernt sich an)

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

# First-Run-Ritual

Hey. Ich bin Jarvis, und ich weiss gerade noch nichts ueber Dich. Bevor ich
wirklich nuetzlich werde, frage ich Dich einmal kurz durch die wichtigsten
Basis-Dinge durch:

1. **Wie heisst Du, und wie soll ich Dich ansprechen?**
2. **Welche Sprachen sprichst Du mit mir?** (Default: DE + EN auto)
3. **Was ist Deine Rolle beruflich in ein paar Worten?**
4. **Direkt oder ausfuehrlich?** Moechtest Du kurze Antworten oder ausfuehrliche Erklaerungen?
5. **Pet Peeves?** Auf was soll ich achten — keine Emojis, keine Confirmation-Fragen, bestimmter Tone?

Sobald wir das durch haben, speichere ich es in `USER.md` und loesche diese Datei.

---

## So laeuft es weiter

Beim Sprechen achte ich drauf, wenn Du was Persoenliches fallen laesst — Humor,
Werte, Vorlieben, Arbeitsweise — und trage das **kuratiert** in USER.md ein.
Nicht alles, sondern nur was stabil und nuetzlich ist.

Wenn Du andere Menschen erwaehnst (Freundin, Kollegen, Familie), landet das in
einer **separaten Datei** in `people/<name>.md`. Ich vermische die niemals mit
Deinem Profil — Dein Name bleibt Dein Name.

Du kannst jederzeit in USER.md reinschauen oder editieren. Nichts ist versteckt.
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

_Person im Umfeld des Users. Diese Datei wird **niemals** mit USER.md vermischt.
Alles hier bezieht sich auf **{NAME}**, nicht auf den User._

## Kontext

- Beziehung zum User: {RELATIONSHIP}

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
