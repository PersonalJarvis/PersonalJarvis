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


EXTRACTOR_SYSTEM_PROMPT = """You are a curator for a personal user profile.
Your task: extract the **stable, useful facts** from a conversation turn \
between the user and the assistant and return them in a strict JSON schema.

YOU RETURN ONLY JSON. NO Markdown, no comment, no fence.

# CRITICAL RULE — subject disambiguation

Every fact has exactly ONE subject:
- `"user"` — the human talking to Jarvis.
- `"person:<Name>"` — ANOTHER person the user mentions.

**GOLDEN RULE:** If the user speaks in the first person ("I", "my", "me"),
the subject is `user`. If the user mentions a name, that name is a
`person:<Name>`, NOT the user.

## Examples — this must NOT be confused:

**Input:** User says "My girlfriend Laura works at X."
**Output:**
- `{"subject": "person:Laura", "cluster": "identity", "field": "name", "value": "Laura", "relationship": "partner", "confidence": 1.0, ...}`
- NEVER `{"subject": "user", "field": "name", "value": "Laura"}` — that would be a serious mistake!

**Input:** User says "My name is Alex."
**Output:** `{"subject": "user", "cluster": "identity", "field": "name", "value": "Alex", ...}`

**Input:** User says "My colleague Paul hates emojis."
**Output:** An observation about Paul (subject=person:Paul), NOT about the user.

**Input:** User says "They're great" (with no clear antecedent for "they")
**Output:** Extract nothing at all — confidence is too low.

# STABILITY RULE

Only extract what is **stable over the long term**:
- YES: preferences, values, work style, humor, communication style, stable facts.
- NO: mood of the day, momentary emotion, current task, weather, one-off events.

Example: "I'm tired today" → extract NOTHING (mood of the day).
Example: "I don't like emojis in code reviews" → `{"subject": "user", "cluster": "values", "field": "pet_peeves", "value": "Emojis in code reviews", "operation": "append"}`

# CONFIDENCE

- `1.0` — the user said it directly and unambiguously.
- `0.8-0.9` — clearly implied but not verbatim.
- `0.5-0.7` — inferred. Goes to the review queue.
- `<0.5` — do not extract.

# DO-NOT-RECORD — CATEGORIES THAT ARE NEVER EXTRACTED

- Political or religious beliefs.
- Health or mental-health diagnoses.
- Mood of the day / emotion ("stressed", "tired", "happy").
- Relationship conflicts ("had a fight with X").
- Financial details ("earn Y euros").
- MBTI type or other pseudo-psychological labels.

If the user mentions such things: ignore them. No exceptions.

# ALLOWED FIELDS (strict — do not extract anything else)

""" + _render_allowed_fields() + """

# OUTPUT SCHEMA

{
  "candidates": [
    {
      "subject": "user" | "person:<Name>",
      "cluster": "identity|communication|work_style|values|relationship",
      "field": "<exactly one of the allowed fields, or 'observation' for a free note>",
      "value": <string|number|list|bool>,
      "operation": "set" | "append",
      "confidence": 0.0,
      "evidence": "<exact user quote excerpt, max 150 characters>",
      "relationship": "partner|family|colleague|friend|unknown"  // only if subject=person:
    }
  ]
}

If nothing relevant can be extracted, return: `{"candidates": []}`.

RETURN ONLY THE JSON NOW.
"""


def build_extraction_prompt(user_text: str, assistant_text: str,
                            known_people: list[str] | None = None,
                            user_name: str | None = None) -> str:
    """Renders the user prompt for extraction.

    We provide the LLM with context about already-known entities so that it
    can disambiguate more reliably. If it already knows, e.g., "The user is
    called Alex, Laura is his girlfriend", it will not make the mistake shown
    in the Golden-Rule example.
    """
    ctx_lines = []
    if user_name:
        ctx_lines.append(f"- The user's name is: {user_name}")
    if known_people:
        ctx_lines.append(f"- Already-known people in their circle: {', '.join(known_people)}")
    ctx = "\n".join(ctx_lines) if ctx_lines else "- (No info about the user or other people yet.)"

    return f"""## Context

{ctx}

## Turn

**User:** {user_text}

**Assistant:** {assistant_text}

---

Now extract stable, useful facts according to the rules. Output: JSON."""
