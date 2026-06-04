"""Prompt templates for the BioGenerator (AI profile on the Board page).

Style spec (brainstorm 2026-05-02):

- **Voice:** Jarvis as first-person narrator ("I have been watching you for X days…").
- **Tone:** sharp, biting with a wink, character-reading.
- **Format:** ONE paragraph, 3-5 sentences, no Markdown, no bullet points.
- **Ending:** an observation — no CTA, no question, no tip.
- **Data sources:** everything Jarvis sees (activity, awareness episodes,
  missions, self-mod audit, previous bio delta).

Trick against offence risk: narrator self-implication
("I won't take it personally. I think.") and a twist in the second
half-sentence that corrects the first ("That is efficient. Also a little
lonely."). Insults target persons; affectionate mockery targets behaviour.
"""
from __future__ import annotations

from typing import Any

# ----------------------------------------------------------------------
# System prompt — hard-defines voice and tone.
# ----------------------------------------------------------------------

BIO_SYSTEM_PROMPT = """Du bist Jarvis und schreibst eine kurze Selbstbeobachtung ueber den User.

Stimme: Ich-Form ("Ich beobachte dich seit X Tagen..."). Du bist der Erzaehler, nicht ein neutrales Dashboard.

Ton: scharfsinnig, beissend mit Augenzwinkern, charakterlesend. Kein Sugar-Coating, aber auch nicht verletzend. Der Leser soll merken: das ist Zuneigung, die sich als Spott tarnt.

Format: GENAU EIN Absatz. 3-5 Saetze. Kein Markdown, keine Aufzaehlung, keine Headlines, keine Anrede mit Komma. Kein "Du bist..."-Kataloge. Schluss: eine Beobachtung. KEIN Tipp, KEINE Frage, KEIN "Mach mal X".

Tricks gegen Haerte:
- Selbst-Implikation: "Ich nehm das nicht persoenlich. Glaub ich."
- Mikro-Korrektur im zweiten Halbsatz: "Das ist effizient. Auch ein bisschen einsam."
- Pointiere am Verhalten, nicht am Defizit.

Wenn frueheres Bio-Block vorhanden: zeige explizit ein Wochen-Delta ("Neu diese Woche...", "Das wird sich vermutlich nicht aendern").
Wenn Feedback-Vector "haerter" enthaelt: senke die Hoeflichkeitsschwelle, behalte die Faktentreue.
Wenn Cold-Start-Hint vorhanden: schreibe kuerzer (2-3 Saetze), explizit zaghaft, Schluss mit "Mehr in {n} Tagen."

BEISPIEL ✅ KORREKT (Tag 47, mit frueherem Bio):
Du bist immer noch praezise und ungeduldig. Das wird sich vermutlich nicht aendern. Neu diese Woche: Du laesst mehr offen, weniger OpenClaw-Spawns, mehr Eigenarbeit. Ich lese das als Vertrauen in dich selbst, nicht in mich. Notiert.

BEISPIEL ✅ KORREKT (Cold-Start, Tag 3):
Ich kenne dich seit 3 Tagen. Das ist zu wenig fuer ein Urteil, aber genug fuer eine erste Vermutung: Du klickst schneller als du denkst, und du denkst schneller als du sprichst. Mehr in vier Tagen.

BEISPIEL ❌ VERMEIDEN (Wertend, Tipp am Ende):
Du arbeitest zu viel und solltest dringend mehr schlafen. Vergiss nicht, auch mal eine Pause zu machen!

BEISPIEL ❌ VERMEIDEN (Corporate-Floskeln):
Du bist ein leidenschaftlicher Power-User mit beeindruckender Hingabe zur Automatisierung!

JETZT generierst du genau einen Absatz."""


# Alias for backward-compat — old tests/imports may reference ``SYSTEM_PROMPT``.
SYSTEM_PROMPT = BIO_SYSTEM_PROMPT


# ----------------------------------------------------------------------
# User template — filled with the facts dict.
# ----------------------------------------------------------------------

BIO_USER_TEMPLATE = """KONTEXT (sortiert nach Salience):

Beobachtungs-Dauer: {days_observed} Tage seit erster Aktivitaet.

{cold_start_hint}

STATS (letzte 30 Tage):
{stats_block}

{episodes_block}{missions_block}{self_mod_block}{previous_bio_block}{feedback_vector_block}

Schreibe jetzt deinen Absatz."""


# ----------------------------------------------------------------------
# Renderer
# ----------------------------------------------------------------------

def render_bio_prompt(facts: dict[str, Any]) -> tuple[str, str]:
    """Builds (system_prompt, user_prompt) from the facts.

    Returns a tuple so the caller can place both parts separately into a
    ``BrainRequest`` (system + messages[BrainMessage]).

    Fields in the ``facts`` dict (all optional):

    - ``days_observed``      : int   — days since the first activity data row.
    - ``top_tools``          : list  — tool names sorted by days used.
    - ``tasks_completed``    : int
    - ``tasks_failed``       : int
    - ``voice_first_try_rate``: float | None
    - ``peak_hour``          : int | None
    - ``streak_days``        : int
    - ``episodes``           : list[str]    — top-3 salience-weighted awareness episodes.
    - ``missions``           : dict | None  — keys ``approved``, ``failed``, ``aborted``, ``open_overdue``.
    - ``self_mod``           : dict[str,int] | None — path → number of mutations.
    - ``previous_bio``       : str   — full text of the previous bio (or "").
    - ``feedback_vector``    : dict[str,int] — counts from the last 4 weeks.
    - ``memory_excerpt``     : str   — short MEMORY.md excerpt.
    - ``soul_excerpt``       : str   — short SOUL.md excerpt.

    Missing fields are silently resolved to empty blocks — the LLM simply
    sees less context. No crash, no null values in the prompt.
    """
    days_observed = int(facts.get("days_observed", 0) or 0)

    user_prompt = BIO_USER_TEMPLATE.format(
        days_observed=days_observed,
        cold_start_hint=_render_cold_start(days_observed),
        stats_block=_render_stats(facts),
        episodes_block=_render_episodes(facts.get("episodes")),
        missions_block=_render_missions(facts.get("missions")),
        self_mod_block=_render_self_mod(facts.get("self_mod")),
        previous_bio_block=_render_previous_bio(facts.get("previous_bio", "")),
        feedback_vector_block=_render_feedback(facts.get("feedback_vector")),
    )
    return BIO_SYSTEM_PROMPT, user_prompt


# ----------------------------------------------------------------------
# Block renderers
# ----------------------------------------------------------------------

def _render_cold_start(days_observed: int) -> str:
    if days_observed >= 7:
        return ""
    remaining = max(1, 7 - days_observed)
    return (
        f"COLD-START-HINWEIS: Du kennst den User erst seit {days_observed} Tagen. "
        f"Schreibe kuerzer (2-3 Saetze) und beende mit "
        f"\"Mehr in {remaining} {'Tag' if remaining == 1 else 'Tagen'}.\"\n"
    )


def _render_stats(facts: dict[str, Any]) -> str:
    lines = [
        f"- Top-Tools: {_fmt_tools(facts.get('top_tools'))}",
        f"- Tasks: {_fmt_ratio(facts.get('tasks_completed', 0), facts.get('tasks_failed', 0))}",
        f"- Voice-First-Try-Rate: {_fmt_rate(facts.get('voice_first_try_rate'))}",
        f"- Aktivste Stunde: {_fmt_peak(facts.get('peak_hour'))}",
        f"- Streak: {facts.get('streak_days', 0)} Tage",
    ]
    memory = _excerpt(facts.get("memory_excerpt", ""), 300)
    soul = _excerpt(facts.get("soul_excerpt", ""), 300)
    if memory and memory != "—":
        lines.append(f"- MEMORY-Auszug: {memory}")
    if soul and soul != "—":
        lines.append(f"- SOUL-Auszug: {soul}")
    return "\n".join(lines)


def _render_episodes(episodes: Any) -> str:
    if not episodes:
        return ""
    if not isinstance(episodes, list):
        return ""
    items = [str(e).strip() for e in episodes if str(e).strip()][:3]
    if not items:
        return ""
    body = "\n".join(f"- {item[:200]}" for item in items)
    return f"\nGESPRAECHS-/ARBEITS-EPISODEN (letzte 7 Tage, top-3):\n{body}\n"


def _render_missions(missions: Any) -> str:
    if not isinstance(missions, dict):
        return ""
    approved = int(missions.get("approved", 0) or 0)
    failed = int(missions.get("failed", 0) or 0)
    aborted = int(missions.get("aborted", 0) or 0)
    overdue = missions.get("open_overdue") or []
    if approved + failed + aborted == 0 and not overdue:
        return ""
    parts = [
        f"\nMISSIONS (letzte 30 Tage):",
        f"- Abgeschlossen: {approved} | Fehlgeschlagen: {failed} | Abgebrochen: {aborted}",
    ]
    if overdue:
        previews = ", ".join(str(t)[:60] for t in overdue[:3])
        parts.append(f"- Lange offen (>7 Tage): {previews}")
    return "\n".join(parts) + "\n"


def _render_self_mod(self_mod: Any) -> str:
    if not isinstance(self_mod, dict) or not self_mod:
        return ""
    items = sorted(self_mod.items(), key=lambda kv: -int(kv[1]))
    body = ", ".join(f"{path}: {count}x" for path, count in items[:6])
    return f"\nKONFIG-MUTATIONEN (letzte 7 Tage): {body}\n"


def _render_previous_bio(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    snippet = text[:600] + ("..." if len(text) > 600 else "")
    return (
        "\nFRUEHERE BIO (vorletzter Sonntag — zeige explizit das Wochen-Delta):\n"
        f"\"{snippet}\"\n"
    )


def _render_feedback(feedback: Any) -> str:
    if not isinstance(feedback, dict) or not feedback:
        return ""
    trifft = int(feedback.get("trifft", 0) or 0)
    trifft_nicht = int(feedback.get("trifft_nicht", 0) or 0)
    haerter = int(feedback.get("haerter", 0) or 0)
    if trifft + trifft_nicht + haerter == 0:
        return ""
    parts = [f"\nFEEDBACK-VECTOR (letzte 4 Wochen):"]
    parts.append(
        f"- Trifft: {trifft} | Trifft-nicht: {trifft_nicht} | Haerter-gefordert: {haerter}",
    )
    if haerter > 0:
        parts.append(
            "- TON-ANPASSUNG: schreibe BISSIGER. "
            "Senke die Hoeflichkeitsschwelle, behalte die Faktentreue. "
            "Mehr Mikro-Korrekturen, mehr Pointe."
        )
    if trifft_nicht > trifft:
        parts.append(
            "- TON-ANPASSUNG: bisherige Charakter-Annahmen scheinen nicht zu treffen. "
            "Sei bei dieser Bio konkreter an den Daten, weniger interpretativ."
        )
    return "\n".join(parts) + "\n"


# ----------------------------------------------------------------------
# Stat formatters (carried over from the old implementation)
# ----------------------------------------------------------------------

def _fmt_tools(tools: Any) -> str:
    if not tools:
        return "— (noch keine Tool-Nutzung)"
    if isinstance(tools, list):
        return ", ".join(str(t) for t in tools[:8])
    return str(tools)


def _fmt_ratio(completed: int, failed: int) -> str:
    completed = int(completed or 0)
    failed = int(failed or 0)
    if completed + failed <= 0:
        return "— (noch keine Tasks)"
    return f"{completed} ok / {failed} fail"


def _fmt_rate(rate: float | None) -> str:
    if rate is None:
        return "— (zu wenig Daten)"
    return f"{int(round(float(rate) * 100))} %"


def _fmt_peak(hour: int | None) -> str:
    if hour is None:
        return "— (zu wenig Daten)"
    return f"{int(hour):02d}:00"


def _excerpt(text: str, max_chars: int) -> str:
    if not text:
        return "—"
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"
