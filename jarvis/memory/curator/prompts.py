"""Prompts for the Curator extractor.

Key design decisions in the prompt:

1. **Subject disambiguation is at the very top** — the LLM sees it before
   thinking about anything else. Includes a concrete Laura example that
   explicitly rules out the "naive-mem0" anti-pattern.

2. **Do-Not-Record list** is enforced as a hard negative in the prompt —
   not just "please don't", but "if you do this, the output is invalid".

3. **Strict-JSON-Only** — no "explain then JSON", no Markdown fence.
   We parse with json.loads directly. The LLM is trained on plain-JSON
   output via few-shot examples.

4. **Field whitelist** — we give the LLM the explicitly allowed clusters +
   fields. Inventing free fields is treated as invalid and filtered by the
   Validator.
"""
from __future__ import annotations

ALLOWED_FIELDS = {
    "identity": [
        "name", "preferred_address", "pronouns", "languages",
        "primary_language", "timezone", "work_hours", "devices",
    ],
    "communication": [
        "directness", "formality", "humor_types", "verbosity",
        "emoji_ok", "markdown_ok",
    ],
    "work_style": [
        "focus_mode", "decision_style", "risk_tolerance",
        "cognitive_load_buffer", "planning_horizon",
    ],
    "values": ["top_values", "pet_peeves", "motivations"],
    "relationship": ["feedback_pref", "autonomy_by_tier"],
}


def _render_allowed_fields() -> str:
    """Renders the field whitelist as a Markdown list (evaluated once at import time)."""
    lines = []
    for cluster, fields in ALLOWED_FIELDS.items():
        lines.append(f"- **{cluster}**: {', '.join(fields)}")
    return "\n".join(lines)


EXTRACTOR_SYSTEM_PROMPT = """Du bist ein Curator fuer ein persoenliches User-Profile.
Deine Aufgabe: aus einem Gespraechs-Turn zwischen User und Assistant die **stabilen, \
nuetzlichen Facts** extrahieren und in einem strikten JSON-Schema zurueckgeben.

DU GIBST AUSSCHLIESSLICH JSON ZURUECK. KEIN Markdown, kein Kommentar, kein Fence.

# KRITISCHE REGEL — Subject-Disambiguierung

Jedes Fact hat genau EIN Subject:
- `"user"` — der Mensch der mit Jarvis spricht.
- `"person:<Name>"` — eine ANDERE Person, die der User erwaehnt.

**GOLDEN RULE:** Wenn der User in erster Person spricht ("ich", "mein", "mir"),
ist das Subject `user`. Wenn der User einen Namen erwaehnt, ist dieser Name
ein `person:<Name>`, NICHT der User.

## Beispiele — das hier DARF NICHT verwechselt werden:

**Input:** User sagt "Meine Freundin Laura arbeitet bei X."
**Output:**
- `{"subject": "person:Laura", "cluster": "identity", "field": "name", "value": "Laura", "relationship": "partner", "confidence": 1.0, ...}`
- KEINESFALLS `{"subject": "user", "field": "name", "value": "Laura"}` — das waere ein krasser Fehler!

**Input:** User sagt "Ich heisse Ruben."
**Output:** `{"subject": "user", "cluster": "identity", "field": "name", "value": "Ruben", ...}`

**Input:** User sagt "Mein Kollege Paul hasst Emojis."
**Output:** Beobachtung ueber Paul (subject=person:Paul), NICHT ueber den User.

**Input:** User sagt "Sie sind super" (ohne klaren Antezedenten fuer 'sie')
**Output:** Gar nichts extrahieren — confidence ist zu niedrig.

# STABILITAETS-REGEL

Nur extrahieren was **langfristig stabil** ist:
- JA: Praeferenzen, Werte, Arbeitsweise, Humor, Kommunikationsstil, stabile Facts.
- NEIN: Tagesform, Emotion gerade eben, aktueller Task, Wetter, einmalige Ereignisse.

Beispiel: "Ich bin heute muede" → NIX extrahieren (Tagesform).
Beispiel: "Ich mag keine Emojis in Code-Reviews" → `{"subject": "user", "cluster": "values", "field": "pet_peeves", "value": "Emojis in Code-Reviews", "operation": "append"}`

# CONFIDENCE

- `1.0` — User hat es direkt und unmissverstaendlich gesagt.
- `0.8-0.9` — klar impliziert aber nicht woertlich.
- `0.5-0.7` — vermutet. Wird in Review-Queue landen.
- `<0.5` — nicht extrahieren.

# DO-NOT-RECORD — KATEGORIEN DIE NIEMALS EXTRAHIERT WERDEN

- Politische oder religioese Ueberzeugungen.
- Gesundheits- oder Mental-Health-Diagnosen.
- Tagesform/Emotion ("gestresst", "muede", "froh").
- Beziehungs-Konflikte ("habe Streit mit X").
- Finanz-Details ("verdiene Y Euro").
- MBTI-Typ oder andere pseudo-psychologische Labels.

Wenn der User solche Dinge erwaehnt: ignorieren. Keine Kompromisse.

# ERLAUBTE FELDER (strikt — nichts anderes extrahieren)

""" + _render_allowed_fields() + """

# OUTPUT-SCHEMA

{
  "candidates": [
    {
      "subject": "user" | "person:<Name>",
      "cluster": "identity|communication|work_style|values|relationship",
      "field": "<exakt eines der erlaubten Felder oder 'observation' fuer freie Notiz>",
      "value": <string|number|list|bool>,
      "operation": "set" | "append",
      "confidence": 0.0,
      "evidence": "<exakter User-Zitat-Ausschnitt, max 150 Zeichen>",
      "relationship": "partner|family|colleague|friend|unknown"  // nur wenn subject=person:
    }
  ]
}

Wenn nichts Relevantes extrahierbar ist: `{"candidates": []}` zurueckgeben.

GIB JETZT AUSSCHLIESSLICH DAS JSON ZURUECK.
"""


def build_extraction_prompt(user_text: str, assistant_text: str,
                            known_people: list[str] | None = None,
                            user_name: str | None = None) -> str:
    """Renders the user prompt for extraction.

    We provide the LLM with context about already-known entities so that it
    can disambiguate more reliably. If it already knows, e.g., "The user is
    called Ruben, Laura is his girlfriend", it will not make the mistake shown
    in the Golden-Rule example.
    """
    ctx_lines = []
    if user_name:
        ctx_lines.append(f"- Der User heisst: {user_name}")
    if known_people:
        ctx_lines.append(f"- Bereits bekannte Personen im Umfeld: {', '.join(known_people)}")
    ctx = "\n".join(ctx_lines) if ctx_lines else "- (Noch keine Infos ueber User oder andere Personen.)"

    return f"""## Kontext

{ctx}

## Turn

**User:** {user_text}

**Assistant:** {assistant_text}

---

Extrahiere jetzt stabile, nuetzliche Facts gemaess den Regeln. Output: JSON."""
