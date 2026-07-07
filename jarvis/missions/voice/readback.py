"""DE/EN voice templates for mission status.

Tone anchor: `jarvis/brain/JARVIS_PERSONA.md` — butler register, no hardcoded
owner name. The spoken persona addresses the user by the name in their profile;
these mission-status templates stay name-neutral so a fresh clone never speaks
the maintainer's name. Hard cap of 280 characters per voice output (TTS latency
+ audio length).

Action/Observation invariant (ADR-0009 §1):
- Templates must **NEVER** read the raw LLM narrative directly.
- `summary_de` from `MissionApproved` payload is OK because it is signed
  by the **Kontrollierer** (source_actor=kontrollierer), not the LLM worker.
- `correction_instruction` from `WorkerCorrectionRequired` is NEVER
  read aloud — only "Iteration N running." as an acknowledgement.

Capability-Honesty (Capability Coupling spec, 2026-05-20):
- `render_approved` accepts an optional ``honesty_check`` parameter.
  When ``honesty_check.honesty_overridden`` is True the approval is a
  false-positive and we render the failure readback instead of a success
  message.  This is the last line of defence before text reaches TTS —
  the gate in ``runner.py`` (``enforce_capability_honesty``) should have
  already corrected the verdict, so this branch should rarely fire in
  production.
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from jarvis.missions.critic.runner import CapabilityHonestyCheck


Lang = Literal["de", "en"]
TemplateKey = Literal[
    "approved",
    "failed",
    "timeout",
    "cancelled",
    "budget_warn_50",
    "budget_warn_80",
    "budget_exceeded",
    "injection_blocked",
    "path_guard_blocked",
    "destructive_confirm",
    "crash_recovery",
    "iteration_running",
]


MAX_VOICE_CHARS: Final[int] = 280


READBACK_TEMPLATES: Final[dict[TemplateKey, dict[Lang, list[str]]]] = {  # i18n-allow: German TTS voice-output templates (paired de/en)
    "approved": {
        "de": [
            "Fertig. {summary}",  # i18n-allow
            "Erledigt. {summary}",
            "Abgeschlossen. {summary}",
        ],
        "en": [
            "Done. {summary}",
            "Completed. {summary}",
        ],
    },
    "failed": {
        "de": [
            "Die Aufgabe ist gescheitert. Grund: {reason}",  # i18n-allow
            "Aufgabe gescheitert. {reason}",
            "Das hat nicht geklappt. {reason}",  # i18n-allow
        ],
        "en": [
            "The task failed. Reason: {reason}",
            "Task failed. {reason}",
        ],
    },
    "timeout": {
        "de": [
            "Die Aufgabe ist in einen Timeout gelaufen.",  # i18n-allow
            "Zeitueberschreitung. Aufgabe abgebrochen.",
        ],
        "en": [
            "The task timed out.",
        ],
    },
    "cancelled": {
        "de": [
            "Aufgabe abgebrochen.",
            "Die Aufgabe wurde gestoppt.",  # i18n-allow
        ],
        "en": [
            "Task cancelled.",
        ],
    },
    "budget_warn_50": {
        "de": [
            "Halbes Budget verbraucht.",
            "Fuenfzig Prozent vom Budget weg.",
        ],
        "en": [
            "Half the budget used.",
        ],
    },
    "budget_warn_80": {
        "de": [
            "Achtzig Prozent vom Budget weg.",
            "Das Budget wird knapp.",  # i18n-allow
        ],
        "en": [
            "Eighty percent of budget used.",
        ],
    },
    "budget_exceeded": {
        "de": [
            "Budget aufgebraucht. Aufgabe abgebrochen.",
            "Das Limit ist erreicht. Stoppe die Aufgabe.",  # i18n-allow
        ],
        "en": [
            "Budget exhausted. Task aborted.",
        ],
    },
    "injection_blocked": {
        "de": [
            "Injection-Versuch erkannt. Aufgabe abgebrochen.",
            "Ein verdaechtiger Output wurde geblockt. Aufgabe gestoppt.",  # i18n-allow
        ],
        "en": [
            "Injection attempt detected. Task terminated.",
        ],
    },
    "path_guard_blocked": {
        "de": [
            "Ein geschuetzter Pfad wurde angefasst. Aufgabe abgebrochen.",  # i18n-allow
            "Geblockter Pfad in der Aufgabe. Stoppe.",
        ],
        "en": [
            "A protected path was touched. Task aborted.",
        ],
    },
    "destructive_confirm": {
        "de": [
            "Das wird {target} loeschen. Bist du sicher? Bitte in der UI bestaetigen.",  # i18n-allow
            "Destruktive Aktion: {target}. Bestaetigung erforderlich.",
        ],
        "en": [
            "This will destroy {target}. Are you sure? Please confirm in the UI.",
        ],
    },
    "crash_recovery": {
        "de": [
            "Eine vorherige Aufgabe wurde wegen Crash abgebrochen.",  # i18n-allow
            "Ich habe eine abgebrochene Aufgabe gefunden und sauber abgeschlossen.",  # i18n-allow
        ],
        "en": [
            "A previous task was aborted due to a crash.",
        ],
    },
    "iteration_running": {
        "de": [
            "Iteration {n} laeuft.",  # i18n-allow
            "Naechster Versuch laeuft.",  # i18n-allow
        ],
        "en": [
            "Iteration {n} running.",
        ],
    },
}


# Machine failure-reason code -> short human phrase. Single source shared
# with jarvis.missions.voice.announcer.MissionAnnouncer so the two voice
# readback paths (direct-TTS listener + announcer bridge) cannot drift apart
# (2026-05-27 hardening finding #7). Keys are the reasons emitted by the
# orchestrator / recovery sweep; the DE and EN sets must stay in parity.
# Keyed by language CODE (str), not the render-API ``Lang`` literal: ``es`` is
# an equal supported product-surface language (CLAUDE.md §1) and MUST be able
# to carry a phrase here even though the ``MissionReadback`` render methods
# themselves still default to the de/en ``Lang`` surface (widening that whole
# API to ``es`` is a separate, larger task). Lookups use ``.get(language, {})``
# so a code without an entry falls back cleanly. The de/en parity gate
# (``test_failure_reason_phrases_de_en_parity``) still guards those two; ``es``
# here carries only the keys that have been translated so far.
FAILURE_REASON_PHRASES: Final[dict[str, dict[str, str]]] = {
    "de": {
        "critic_loop_exhausted": "Drei Versuche haben nicht gereicht.",  # i18n-allow
        "critic_rejected": "Die Prüfung war nicht zufrieden.",  # i18n-allow
        "task_error": "Der Worker ist abgebrochen.",  # i18n-allow
        "attempts_timed_out": "Das Zeitlimit wurde überschritten.",  # i18n-allow (DE TTS phrase)
        "budget_exceeded": "Das Kostenlimit ist erreicht.",  # i18n-allow
        "decompose_failed": "Die Aufgabe konnte ich nicht zerlegen.",  # i18n-allow
        "crash_recovery": "Eine alte Mission wurde aufgeräumt.",  # i18n-allow
        "interrupted": "Eine laufende Mission wurde unterbrochen.",  # i18n-allow (DE TTS phrase)
        "empty_diff": "Es wurden keine Dateien geschrieben.",  # i18n-allow
        "critic_unavailable": "Der Prüfer ist abgestürzt, die Arbeit liegt im Worktree.",  # i18n-allow
        "worktree_setup_failed": "Ich konnte keinen Arbeitsbereich anlegen.",  # i18n-allow
        "git_missing": "Jarvis-Agents brauchen eine Git-Installation im PATH.",  # i18n-allow
        "git_not_a_repository": (
            "Jarvis-Agents brauchen einen Git-Checkout, bitte über den "  # i18n-allow
            "Git-Installer installieren, nicht als ZIP."  # i18n-allow
        ),
        # error_class keys (looked up BEFORE the reason key; see
        # failure_phrase_key). Same table so announcer + direct-TTS listener
        # cannot drift (2026-05-27 finding #7).
        "provider_auth": "Die Anmeldung beim KI-Anbieter ist ungültig oder abgelaufen.",  # i18n-allow
        "provider_quota": "Das Kontingent des KI-Anbieters ist erschöpft.",  # i18n-allow
        "provider_unreachable": "Der KI-Anbieter ist gerade nicht erreichbar.",  # i18n-allow
        "worker_timeout": "Der Worker hat das Zeitlimit überschritten.",  # i18n-allow
    },
    "en": {
        "critic_loop_exhausted": "Three attempts were not enough.",
        "critic_rejected": "The review wasn't satisfied.",
        "task_error": "The worker aborted.",
        "attempts_timed_out": "The time limit was reached.",
        "budget_exceeded": "The cost limit was reached.",
        "decompose_failed": "I could not break the task down.",
        "crash_recovery": "An old mission was cleaned up.",
        "interrupted": "A running mission was interrupted; the partial results are available.",
        "empty_diff": "No files were written.",
        "critic_unavailable": (
            "The reviewer crashed; the work is preserved in the worktree."
        ),
        "worktree_setup_failed": "I could not create a workspace.",
        "git_missing": "Jarvis-Agents require git to be installed and on PATH.",
        "git_not_a_repository": (
            "Jarvis-Agents require a git checkout (install via the "
            "git-based installer, not a ZIP download)."
        ),
        "provider_auth": "The AI provider sign-in is invalid or expired.",
        "provider_quota": "The AI provider's quota is exhausted.",
        "provider_unreachable": "The AI provider is currently unreachable.",
        "worker_timeout": "The worker hit its time limit.",
    },
    # Spanish is an equal supported product-surface language (CLAUDE.md §1).
    # Only the two git-setup reason keys added with this fix carry ``es`` for
    # now — the rest of the table is de/en pending a broader translation pass;
    # a missing key here falls back via ``.get(language, {})``.
    "es": {
        "git_missing": "Jarvis-Agents necesitan que git esté instalado y en el PATH.",  # i18n-allow: Spanish TTS product-surface phrase
        "git_not_a_repository": (
            "Jarvis-Agents necesitan una copia de git (instala con el "  # i18n-allow: Spanish TTS product-surface phrase
            "instalador de git, no con una descarga ZIP)."  # i18n-allow: Spanish TTS product-surface phrase
        ),
    },
}


def failure_phrase_key(reason: str, error_class: str | None) -> str:
    """Pick the phrase-table key for a failed mission.

    A populated ``error_class`` (e.g. ``provider_auth``) is more specific
    than the mission-level ``reason`` (often the generic ``task_error``), so
    it wins whenever the table carries it. Falls back to the reason's short
    form. Single source for the announcer AND the direct-TTS listener.
    """
    ec = (error_class or "").strip()
    if ec and ec in FAILURE_REASON_PHRASES["en"]:
        return ec
    return (reason or "").split(":", 1)[0].strip()


def _truncate(text: str, max_chars: int = MAX_VOICE_CHARS) -> str:
    """Truncate to max_chars; no suffix so TTS does not say '...'.

    We cut hard intentionally — voice templates should be short to begin
    with. If a {summary} insert is too long, this will be caught in
    the test (`test_render_*_truncates_long_summary`) and the insert
    should be capped BEFORE rendering.
    """
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rstrip()
    return cut


class MissionReadback:
    """Render methods for mission status voice outputs.

    Uses a dedicated PhrasePicker with anti_repeat_window=3 — prevents
    "Sir, done" from being spoken three times in a row when three
    missions complete in quick succession.
    """

    def __init__(self, *, anti_repeat_window: int = 3) -> None:
        self._window = anti_repeat_window
        # Per-(key, lang) deque of recently played templates
        self._recent: dict[tuple[str, str], deque[str]] = {}

    def _pick(self, key: TemplateKey, lang: Lang) -> str:
        """Choose a template, avoiding immediate repetition."""
        pool = READBACK_TEMPLATES.get(key, {}).get(lang, [])
        if not pool:
            other: Lang = "en" if lang == "de" else "de"
            pool = READBACK_TEMPLATES.get(key, {}).get(other, [])
        if not pool:
            return ""

        cache_key = (key, lang)
        window = min(self._window, max(1, len(pool) - 1))
        recent = self._recent.setdefault(cache_key, deque(maxlen=window))
        candidates = [p for p in pool if p not in recent]
        if not candidates:
            candidates = pool
        # Deterministic for tests: first candidate. PhrasePicker
        # uses random.choice — we keep it simple + reproducible.
        choice = candidates[0]
        recent.append(choice)
        return choice

    # --- Render methods ---

    def render_approved(
        self,
        *,
        summary: str = "",
        language: Lang = "de",
        honesty_check: CapabilityHonestyCheck | None = None,
    ) -> str:
        """Render a success readback for an approved mission.

        Capability-Honesty guard: if ``honesty_check`` is provided and its
        ``honesty_overridden`` flag is ``True``, the approval is a false-
        positive (worker claimed success without making any tool call).  In
        that case we render the failure readback using the corrected
        ``summary_de`` from the overridden verdict instead of a success
        message.  This is a last-resort defence — the gate in
        ``runner.py:enforce_capability_honesty`` should already have
        overridden the verdict before the Kontrollierer signed the approval.
        """
        # --- Capability-Honesty last-resort check ---
        if honesty_check is not None and honesty_check.honesty_overridden:
            # The "approved" verdict is a false-positive: render failure.
            override_reason = (
                honesty_check.verdict.summary_de
                or "Konnte ich nicht ausführen — kein Tool-Aufruf."  # i18n-allow: German TTS fallback phrase
            )
            return self.render_failed(reason=override_reason, language=language)

        template = self._pick("approved", language)
        if not template:
            return ""
        # Insert cap so the final string does not exceed 280 chars
        max_insert = MAX_VOICE_CHARS - len(template) + len("{summary}")
        safe_summary = (summary or "Aufgabe erledigt.").strip()
        if len(safe_summary) > max_insert:
            safe_summary = safe_summary[:max_insert].rstrip()
        return _truncate(template.format(summary=safe_summary))

    def render_failed(
        self,
        *,
        reason: str = "",
        language: Lang = "de",
        error_class: str | None = None,
    ) -> str:
        short_reason = failure_phrase_key(reason, error_class)
        # crash_recovery and interrupted are swept/interrupted previous-session
        # missions, not live task failures — speak a dedicated non-alarming phrase
        # instead of framing them as "gescheitert. Grund: <reason>"
        # (2026-05-27 finding #7; interrupted added 2026-06-07 for commit 13b86605).
        if short_reason == "crash_recovery":
            return self.render_crash_recovery(language=language)
        if short_reason == "interrupted":
            phrase = FAILURE_REASON_PHRASES.get(language, {}).get("interrupted", "")
            return _truncate(phrase) if phrase else self.render_crash_recovery(language=language)
        template = self._pick("failed", language)
        if not template:
            return ""
        # Map a known machine reason code to a friendly human phrase so a
        # raw snake_case token is never spoken. Shared with the announcer via
        # FAILURE_REASON_PHRASES. Unmapped reasons fall back to the raw text.
        mapped = FAILURE_REASON_PHRASES.get(language, {}).get(short_reason)
        safe_reason = mapped if mapped else (reason or "unbekannter Fehler").strip()  # i18n-allow: German TTS fallback phrase
        max_insert = MAX_VOICE_CHARS - len(template) + len("{reason}")
        if len(safe_reason) > max_insert:
            safe_reason = safe_reason[:max_insert].rstrip()
        return _truncate(template.format(reason=safe_reason))

    def render_timeout(self, *, language: Lang = "de") -> str:
        return _truncate(self._pick("timeout", language))

    def render_cancelled(self, *, language: Lang = "de") -> str:
        return _truncate(self._pick("cancelled", language))

    def render_budget_warn(self, *, pct: int, language: Lang = "de") -> str:
        if pct >= 80:
            key: TemplateKey = "budget_warn_80"
        else:
            key = "budget_warn_50"
        return _truncate(self._pick(key, language))

    def render_budget_exceeded(self, *, language: Lang = "de") -> str:
        return _truncate(self._pick("budget_exceeded", language))

    def render_injection_blocked(self, *, language: Lang = "de") -> str:
        return _truncate(self._pick("injection_blocked", language))

    def render_path_guard_blocked(self, *, language: Lang = "de") -> str:
        return _truncate(self._pick("path_guard_blocked", language))

    def render_destructive_confirm(
        self, *, target: str = "diese Aktion", language: Lang = "de"
    ) -> str:
        template = self._pick("destructive_confirm", language)
        if not template:
            return ""
        max_insert = MAX_VOICE_CHARS - len(template) + len("{target}")
        safe_target = (target or "diese Aktion").strip()
        if len(safe_target) > max_insert:
            safe_target = safe_target[:max_insert].rstrip()
        return _truncate(template.format(target=safe_target))

    def render_crash_recovery(self, *, language: Lang = "de") -> str:
        return _truncate(self._pick("crash_recovery", language))

    def render_iteration_running(self, *, n: int, language: Lang = "de") -> str:
        template = self._pick("iteration_running", language)
        if not template:
            return ""
        return _truncate(template.format(n=n))


__all__ = [
    "MAX_VOICE_CHARS",
    "MissionReadback",
    "READBACK_TEMPLATES",
    "TemplateKey",
    "failure_phrase_key",
]
