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


# Every supported reply language (``SUPPORTED_REPLY_LANGUAGES`` minus "auto");
# all locales are equal, so every table below carries each of them.
Lang = Literal["de", "en", "es", "pt"]
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
        "es": [
            "Hecho. {summary}",  # i18n-allow: Spanish TTS
            "Listo. {summary}",  # i18n-allow: Spanish TTS
        ],
        "pt": [
            "Feito. {summary}",  # i18n-allow: PT TTS
            "Pronto. {summary}",  # i18n-allow: PT TTS
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
        "es": [
            "La tarea ha fallado. Motivo: {reason}",  # i18n-allow: Spanish TTS
            "Tarea fallida. {reason}",  # i18n-allow: Spanish TTS
        ],
        "pt": [
            "A tarefa falhou. Motivo: {reason}",  # i18n-allow: PT TTS
            "Tarefa falhada. {reason}",  # i18n-allow: PT TTS
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
        "es": [
            "La tarea superó el tiempo límite.",  # i18n-allow: Spanish TTS
        ],
        "pt": [
            "A tarefa excedeu o tempo limite.",  # i18n-allow: PT TTS
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
        "es": [
            "Tarea cancelada.",  # i18n-allow: Spanish TTS
        ],
        "pt": [
            "Tarefa cancelada.",  # i18n-allow: PT TTS
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
        "es": [
            "La mitad del presupuesto está gastada.",  # i18n-allow: Spanish TTS
        ],
        "pt": [
            "Metade do orçamento já foi gasta.",  # i18n-allow: PT TTS
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
        "es": [
            "El ochenta por ciento del presupuesto está gastado.",  # i18n-allow: Spanish TTS
        ],
        "pt": [
            "Oitenta por cento do orçamento já foi gasto.",  # i18n-allow: PT TTS
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
        "es": [
            "Presupuesto agotado. Tarea cancelada.",  # i18n-allow: Spanish TTS
        ],
        "pt": [
            "Orçamento esgotado. Tarefa cancelada.",  # i18n-allow: PT TTS
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
        "es": [
            "Intento de inyección detectado. Tarea detenida.",  # i18n-allow: Spanish TTS
        ],
        "pt": [
            "Tentativa de injeção detetada. Tarefa parada.",  # i18n-allow: PT TTS
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
        "es": [
            "Se tocó una ruta protegida. Tarea cancelada.",  # i18n-allow: Spanish TTS
        ],
        "pt": [
            "Foi tocado um caminho protegido. Tarefa cancelada.",  # i18n-allow: PT TTS
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
        "es": [
            "Esto eliminará {target}. ¿Seguro? Confírmalo en la interfaz.",  # i18n-allow
        ],
        "pt": [
            "Isto vai eliminar {target}. Tens a certeza? Confirma na interface.",  # i18n-allow
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
        "es": [
            "Una tarea anterior se canceló por un fallo.",  # i18n-allow: Spanish TTS
        ],
        "pt": [
            "Uma tarefa anterior foi cancelada devido a uma falha.",  # i18n-allow: PT TTS
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
        "es": [
            "Iteración {n} en curso.",  # i18n-allow: Spanish TTS
        ],
        "pt": [
            "Iteração {n} em curso.",  # i18n-allow: PT TTS
        ],
    },
}


# Machine failure-reason code -> short human phrase. Single source shared
# with jarvis.missions.voice.announcer.MissionAnnouncer so the two voice
# readback paths (direct-TTS listener + announcer bridge) cannot drift apart
# (2026-05-27 hardening finding #7). Keys are the reasons emitted by the
# orchestrator / recovery sweep; the DE and EN sets must stay in parity.
# Keyed by language CODE (str), not the render-API ``Lang`` literal: ``es`` is
# an equal supported product-surface language (AGENTS.md §1) and MUST be able
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
        "review_time_budget_exhausted": (  # i18n-allow: German TTS phrase
            "Das Prüfzeitlimit ist erreicht; das Zwischenergebnis ist verfügbar."  # i18n-allow
        ),
        "budget_exceeded": "Das Kostenlimit ist erreicht.",  # i18n-allow
        "decompose_failed": "Die Aufgabe konnte ich nicht zerlegen.",  # i18n-allow
        "crash_recovery": "Eine alte Mission wurde aufgeräumt.",  # i18n-allow
        "interrupted": "Eine laufende Mission wurde unterbrochen.",  # i18n-allow (DE TTS phrase)
        "empty_diff": "Es wurden keine Dateien geschrieben.",  # i18n-allow
        "critic_unavailable": "Der Prüfer ist abgestürzt, die Arbeit liegt im Worktree.",  # i18n-allow
        "worktree_setup_failed": "Ich konnte keinen Arbeitsbereich anlegen.",  # i18n-allow
        "checkpoint_missing": (
            "Der gesicherte Zwischenstand fehlt, ich kann nicht fortsetzen."  # i18n-allow
        ),
        "git_missing": "{agents} brauchen eine Git-Installation im PATH.",  # i18n-allow
        "git_not_a_repository": (
            "{agents} brauchen einen Git-Checkout, bitte über den "  # i18n-allow
            "Git-Installer installieren, nicht als ZIP."  # i18n-allow
        ),
        "source_checkout_unavailable": (
            "Diese {agent}-Aufgabe braucht den "  # i18n-allow: German TTS
            "Quellcode-Checkout, der in dieser "  # i18n-allow: German TTS
            "Installation nicht verfügbar ist."  # i18n-allow: German TTS
        ),
        # error_class keys (looked up BEFORE the reason key; see
        # failure_phrase_key). Same table so announcer + direct-TTS listener
        # cannot drift (2026-05-27 finding #7).
        "provider_auth": (
            "Die Anmeldung beim KI-Anbieter ist ungültig oder abgelaufen."  # i18n-allow
        ),
        "provider_quota": "Das Kontingent des KI-Anbieters ist erschöpft.",  # i18n-allow
        "provider_unreachable": "Der KI-Anbieter ist gerade nicht erreichbar.",  # i18n-allow
        "worker_timeout": "Der Worker hat das Zeitlimit überschritten.",  # i18n-allow
    },
    "en": {
        "critic_loop_exhausted": "Three attempts were not enough.",
        "critic_rejected": "The review wasn't satisfied.",
        "task_error": "The worker aborted.",
        "attempts_timed_out": "The time limit was reached.",
        "review_time_budget_exhausted": (
            "The review time budget was reached; partial output is available."
        ),
        "budget_exceeded": "The cost limit was reached.",
        "decompose_failed": "I could not break the task down.",
        "crash_recovery": "An old mission was cleaned up.",
        "interrupted": "A running mission was interrupted; the partial results are available.",
        "empty_diff": "No files were written.",
        "critic_unavailable": (
            "The reviewer crashed; the work is preserved in the worktree."
        ),
        "worktree_setup_failed": "I could not create a workspace.",
        "checkpoint_missing": "The saved progress is missing, so I cannot continue.",
        "git_missing": "{agents} require git to be installed and on PATH.",
        "git_not_a_repository": (
            "{agents} require a git checkout (install via the "
            "git-based installer, not a ZIP download)."
        ),
        "source_checkout_unavailable": (
            "This {agent} task requires the source checkout, which is "
            "not available in this installation."
        ),
        "provider_auth": "The AI provider sign-in is invalid or expired.",
        "provider_quota": "The AI provider's quota is exhausted.",
        "provider_unreachable": "The AI provider is currently unreachable.",
        "worker_timeout": "The worker hit its time limit.",
    },
    # Spanish and European Portuguese are equal supported product-surface
    # languages (AGENTS.md §2); both carry the full de/en key set, guarded by
    # ``test_failure_reason_phrases_all_locales_parity``.
    "es": {
        "critic_loop_exhausted": "Tres intentos no han bastado.",  # i18n-allow: Spanish TTS
        "critic_rejected": "La revisión no quedó satisfecha.",  # i18n-allow: Spanish TTS
        "task_error": "El worker se ha interrumpido.",  # i18n-allow: Spanish TTS
        "attempts_timed_out": "Se alcanzó el tiempo límite.",  # i18n-allow: Spanish TTS
        "review_time_budget_exhausted": (  # i18n-allow: Spanish TTS phrase
            "Se agotó el tiempo de revisión; el resultado parcial está "  # i18n-allow
            "disponible."  # i18n-allow
        ),
        "budget_exceeded": "Se alcanzó el límite de coste.",  # i18n-allow: Spanish TTS
        "decompose_failed": "No he podido dividir la tarea.",  # i18n-allow: Spanish TTS
        "crash_recovery": "Se ha limpiado una misión antigua.",  # i18n-allow: Spanish TTS
        "interrupted": (
            "Se interrumpió una misión en curso; los resultados "  # i18n-allow: Spanish TTS
            "parciales están disponibles."  # i18n-allow: Spanish TTS
        ),
        "empty_diff": "No se escribió ningún archivo.",  # i18n-allow: Spanish TTS
        "critic_unavailable": (
            "El revisor ha fallado; el trabajo se conserva en el worktree."  # i18n-allow
        ),
        "worktree_setup_failed": "No he podido crear un espacio de trabajo.",  # i18n-allow
        "checkpoint_missing": (
            "Falta el progreso guardado, así que no puedo continuar."  # i18n-allow: Spanish TTS
        ),
        "git_missing": "{agents} necesitan que git esté instalado y en el PATH.",  # i18n-allow
        "git_not_a_repository": (
            "{agents} necesitan una copia de git (instala con el "  # i18n-allow
            "instalador de git, no con una descarga ZIP)."  # i18n-allow
        ),
        "source_checkout_unavailable": (
            "Esta tarea de {agent} necesita el "  # i18n-allow: Spanish TTS
            "repositorio del código fuente, que no está "  # i18n-allow: Spanish TTS
            "disponible en esta instalación."  # i18n-allow: Spanish TTS
        ),
        "provider_auth": (
            "El inicio de sesión del proveedor de IA no es válido o ha caducado."  # i18n-allow
        ),
        "provider_quota": "La cuota del proveedor de IA está agotada.",  # i18n-allow: Spanish TTS
        "provider_unreachable": "El proveedor de IA no está disponible ahora.",  # i18n-allow
        "worker_timeout": "El worker superó su tiempo límite.",  # i18n-allow: Spanish TTS
    },
    "pt": {
        "critic_loop_exhausted": "Três tentativas não chegaram.",  # i18n-allow: PT TTS
        "critic_rejected": "A revisão não ficou satisfeita.",  # i18n-allow: PT TTS
        "task_error": "O worker foi interrompido.",  # i18n-allow: PT TTS
        "attempts_timed_out": "O tempo limite foi atingido.",  # i18n-allow: PT TTS
        "review_time_budget_exhausted": (  # i18n-allow: Portuguese TTS phrase
            "O tempo de revisão esgotou-se; o resultado parcial está "  # i18n-allow
            "disponível."  # i18n-allow
        ),
        "budget_exceeded": "O limite de custo foi atingido.",  # i18n-allow: PT TTS
        "decompose_failed": "Não consegui dividir a tarefa.",  # i18n-allow: PT TTS
        "crash_recovery": "Uma missão antiga foi limpa.",  # i18n-allow: PT TTS
        "interrupted": (
            "Uma missão em curso foi interrompida; os resultados "  # i18n-allow: PT TTS
            "parciais estão disponíveis."  # i18n-allow: PT TTS
        ),
        "empty_diff": "Não foi escrito nenhum ficheiro.",  # i18n-allow: PT TTS
        "critic_unavailable": (
            "O revisor falhou; o trabalho está guardado no worktree."  # i18n-allow: PT TTS
        ),
        "worktree_setup_failed": "Não consegui criar um espaço de trabalho.",  # i18n-allow: PT TTS
        "checkpoint_missing": (
            "Falta o progresso guardado, por isso não posso continuar."  # i18n-allow: PT TTS
        ),
        "git_missing": (
            "{agents} precisam que o git esteja instalado e no PATH."  # i18n-allow: PT TTS
        ),
        "git_not_a_repository": (
            "{agents} precisam de uma cópia git (instala com o "  # i18n-allow: PT TTS
            "instalador do git, não com uma transferência ZIP)."  # i18n-allow: PT TTS
        ),
        "source_checkout_unavailable": (
            "Esta tarefa de {agent} precisa do "  # i18n-allow: PT TTS
            "repositório do código-fonte, que não está "  # i18n-allow: PT TTS
            "disponível nesta instalação."  # i18n-allow: PT TTS
        ),
        "provider_auth": (
            "O início de sessão no fornecedor de IA é inválido ou expirou."  # i18n-allow: PT TTS
        ),
        "provider_quota": "A quota do fornecedor de IA está esgotada.",  # i18n-allow: PT TTS
        "provider_unreachable": "O fornecedor de IA não está acessível agora.",  # i18n-allow
        "worker_timeout": "O worker excedeu o tempo limite.",  # i18n-allow: PT TTS
    },
}


def approved_summary(payload: object, language: str) -> str:
    """The runtime-signed approval summary to speak in *language*.

    ``summary_de``/``summary_en`` cover German and English; ``summary_local``
    carries the dispatch language for every other locale. An event from
    before ``summary_local`` existed falls back to ``summary_en``.
    """
    if language == "de":
        return str(getattr(payload, "summary_de", "") or "")
    if language != "en":
        local = str(getattr(payload, "summary_local", "") or "")
        if local:
            return local
    return str(getattr(payload, "summary_en", "") or "")


def render_agent_brand(phrase: str) -> str:
    """Resolve the ``{agent}``/``{agents}`` placeholders to the live brand.

    The public agent-system name follows the wake-word-derived assistant name
    ("Ruben" -> "Ruben-Agent") for ANY configured wake word (2026-07-17
    rebrand). Read fresh per call so a wake-word change applies without a
    restart; any resolution failure falls back to the neutral
    "Assistant-Agent" — a spoken phrase must never carry a raw placeholder.
    """
    if "{agent" not in phrase:
        return phrase
    try:
        from jarvis.brain.assistant_name import agent_brand
        from jarvis.core.config import load_config

        brand = agent_brand(load_config())
    except Exception:  # noqa: BLE001 — never break a voice readback on config trouble
        from jarvis.brain.assistant_name import agent_brand_from_name

        brand = agent_brand_from_name("")
    return phrase.replace("{agents}", f"{brand}s").replace("{agent}", brand)


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


# WAITING_CAPACITY readback (jarvis/missions/capacity.py). Built from runtime
# counts only — never worker text (ADR-0009 §1). Keys mirror
# CAPACITY_WAIT_REASONS in jarvis/missions/events.py; parity is guarded by
# tests/missions/test_capacity_wait_parity.py.
CAPACITY_WAIT_PHRASES: Final[dict[str, dict[str, str]]] = {
    "de": {
        "provider_quota": "{provider}-Kapazität momentan ausgeschöpft.",  # i18n-allow
        "provider_auth": "Die {provider}-Anmeldung ist abgelaufen.",  # i18n-allow
        "provider_unavailable": "{provider} ist momentan nicht nutzbar.",  # i18n-allow
        "paid_cap_reached": (
            "Die Kostengrenze dieser Mission für {provider} ist erreicht."  # i18n-allow
        ),
        "paid_daily_cap_reached": (
            "Das Tageslimit für automatische {provider}-Nutzung ist erreicht."  # i18n-allow
        ),
        "paid_consent_revoked": (
            "Die automatische {provider}-Nutzung wurde ausgeschaltet."  # i18n-allow
        ),
        "saved": "Der bisherige Stand wurde gesichert.",  # i18n-allow
        "progress": "{done} von {total} Teilschritten erledigt, {open} offen.",  # i18n-allow
        "progress_single": "Die Aufgabe selbst ist noch offen.",  # i18n-allow
        "file": "1 Datei gespeichert.",  # i18n-allow
        "files": "{files} Dateien gespeichert.",  # i18n-allow
        "wait": "Die Mission wartet, bis wieder Kapazität verfügbar ist.",  # i18n-allow
        "wait_auth": "Die Mission wartet, bis du dich wieder anmeldest.",  # i18n-allow
        "paid": (
            "Kostenpflichtige API-Nutzung nur mit deiner ausdrücklichen "  # i18n-allow
            "Freigabe unter Artefakte."  # i18n-allow
        ),
    },
    "en": {
        "provider_quota": "{provider} capacity is used up for now.",
        "provider_auth": "The {provider} sign-in has expired.",
        "provider_unavailable": "{provider} cannot be used right now.",
        "paid_cap_reached": "This mission's cost limit for {provider} is reached.",
        "paid_daily_cap_reached": "The daily limit for automatic {provider} use is reached.",
        "paid_consent_revoked": "Automatic {provider} use was switched off.",
        "saved": "The progress so far has been saved.",
        "progress": "{done} of {total} steps done, {open} open.",
        "progress_single": "The task itself is still open.",
        "file": "1 file saved.",
        "files": "{files} files saved.",
        "wait": "The mission is waiting until capacity is available again.",
        "wait_auth": "The mission is waiting until you sign in again.",
        "paid": "Paid API use only with your explicit approval in Artifacts.",
    },
    "es": {
        "provider_quota": "La capacidad de {provider} está agotada por ahora.",  # i18n-allow
        "provider_auth": "El inicio de sesión de {provider} ha caducado.",  # i18n-allow
        "provider_unavailable": "{provider} no se puede usar ahora mismo.",  # i18n-allow
        "paid_cap_reached": (
            "Se alcanzó el límite de coste de esta misión para {provider}."  # i18n-allow
        ),
        "paid_daily_cap_reached": (
            "Se alcanzó el límite diario de uso automático de {provider}."  # i18n-allow
        ),
        "paid_consent_revoked": (
            "El uso automático de {provider} se ha desactivado."  # i18n-allow: Spanish TTS
        ),
        "saved": "El progreso hasta ahora se ha guardado.",  # i18n-allow: Spanish TTS
        "progress": "{done} de {total} pasos hechos, {open} pendientes.",  # i18n-allow: Spanish TTS
        "progress_single": "La tarea en sí sigue pendiente.",  # i18n-allow: Spanish TTS
        "file": "1 archivo guardado.",  # i18n-allow: Spanish TTS
        "files": "{files} archivos guardados.",  # i18n-allow: Spanish TTS
        "wait": "La misión espera hasta que vuelva a haber capacidad.",  # i18n-allow: Spanish TTS
        "wait_auth": "La misión espera hasta que vuelvas a iniciar sesión.",  # i18n-allow
        "paid": (
            "Uso de API de pago solo con tu aprobación expresa "  # i18n-allow: Spanish TTS
            "en Artefactos."  # i18n-allow: Spanish TTS
        ),
    },
    "pt": {
        "provider_quota": "A capacidade de {provider} está esgotada por agora.",  # i18n-allow
        "provider_auth": "O início de sessão em {provider} expirou.",  # i18n-allow: PT TTS
        "provider_unavailable": "{provider} não pode ser usado neste momento.",  # i18n-allow
        "paid_cap_reached": (
            "O limite de custo desta missão para {provider} foi atingido."  # i18n-allow: PT TTS
        ),
        "paid_daily_cap_reached": (
            "O limite diário de uso automático de {provider} foi atingido."  # i18n-allow: PT TTS
        ),
        "paid_consent_revoked": (
            "O uso automático de {provider} foi desligado."  # i18n-allow: PT TTS
        ),
        "saved": "O progresso até agora foi guardado.",  # i18n-allow: PT TTS
        "progress": "{done} de {total} passos feitos, {open} por fazer.",  # i18n-allow: PT TTS
        "progress_single": "A tarefa em si ainda está por fazer.",  # i18n-allow: PT TTS
        "file": "1 ficheiro guardado.",  # i18n-allow: PT TTS
        "files": "{files} ficheiros guardados.",  # i18n-allow: PT TTS
        "wait": "A missão aguarda até haver capacidade outra vez.",  # i18n-allow: PT TTS
        "wait_auth": "A missão aguarda até voltares a iniciar sessão.",  # i18n-allow: PT TTS
        "paid": (
            "Uso de API paga só com a tua aprovação explícita "  # i18n-allow: PT TTS
            "em Artefactos."  # i18n-allow: PT TTS
        ),
    },
}

_MAX_PROVIDER_CHARS: Final[int] = 40

# Display names for worker provider slugs in the capacity readback. A slug
# without an entry is spoken as-is.
_PROVIDER_DISPLAY: Final[dict[str, str]] = {
    "claude": "Claude",
    "claude-api": "Claude",
    "codex": "ChatGPT",
    "chatgpt": "ChatGPT",
    "openai": "OpenAI",
    "gemini": "Gemini",
    "antigravity": "Gemini",
    "grok": "Grok",
    "openrouter": "OpenRouter",
    "nvidia": "NVIDIA",
}


# Spoken name for a provider slug the runtime could not resolve.
_PROVIDER_FALLBACK_NAME: Final[dict[str, str]] = {
    "de": "KI-Anbieter",  # i18n-allow: German TTS
    "en": "AI provider",
    "es": "proveedor de IA",  # i18n-allow: Spanish TTS
    "pt": "fornecedor de IA",  # i18n-allow: PT TTS
}

# Spoken defaults for an empty summary / reason / destructive target.
_EMPTY_SUMMARY: Final[dict[str, str]] = {
    "de": "Aufgabe erledigt.",  # i18n-allow: German TTS
    "en": "Task completed.",
    "es": "Tarea completada.",  # i18n-allow: Spanish TTS
    "pt": "Tarefa concluída.",  # i18n-allow: PT TTS
}
_UNKNOWN_REASON: Final[dict[str, str]] = {
    "de": "unbekannter Fehler",  # i18n-allow: German TTS
    "en": "unknown error",
    "es": "error desconocido",  # i18n-allow: Spanish TTS
    "pt": "erro desconhecido",  # i18n-allow: PT TTS
}
_NO_TOOL_CALL: Final[dict[str, str]] = {
    "de": "Konnte ich nicht ausführen — kein Tool-Aufruf.",  # i18n-allow: German TTS
    "en": "I could not do that — no tool call was made.",
    "es": "No pude hacerlo: no hubo ninguna llamada a herramienta.",  # i18n-allow: Spanish TTS
    "pt": "Não consegui fazer isso: não houve nenhuma chamada a ferramenta.",  # i18n-allow: PT TTS
}
_DEFAULT_TARGET: Final[dict[str, str]] = {
    "de": "diese Aktion",  # i18n-allow: German TTS
    "en": "this action",
    "es": "esta acción",  # i18n-allow: Spanish TTS
    "pt": "esta ação",  # i18n-allow: PT TTS
}


def _localized(table: dict[str, str], language: str) -> str:
    """Phrase for *language*; an unknown code speaks English."""
    return table.get(language, table["en"])


def render_capacity_wait(
    *,
    reason: str,
    provider: str | None,
    steps_done: int,
    steps_total: int,
    files_saved: int,
    checkpoint_saved: bool,
    language: Lang = "de",
) -> str:
    """Short readback for a mission parked in WAITING_CAPACITY: why it stopped,
    what is done, what is open, and the options (wait / approve paid use)."""
    table = CAPACITY_WAIT_PHRASES.get(language, CAPACITY_WAIT_PHRASES["en"])
    slug = (provider or "").strip()
    fallback = _PROVIDER_FALLBACK_NAME.get(language, _PROVIDER_FALLBACK_NAME["en"])
    name = (_PROVIDER_DISPLAY.get(slug.lower(), slug) or fallback)[:_MAX_PROVIDER_CHARS]
    head = [table.get(reason, table["provider_unavailable"]).format(provider=name)]
    if checkpoint_saved:
        head.append(table["saved"])
    tail = [table["wait_auth" if reason == "provider_auth" else "wait"], table["paid"]]
    progress = (
        table["progress"].format(
            done=steps_done, total=steps_total, open=max(steps_total - steps_done, 0),
        )
        if steps_total > 1
        else table["progress_single"]
    )
    files = (
        table["file"] if files_saved == 1
        else table["files"].format(files=files_saved) if files_saved > 1
        else ""
    )
    # Why it stopped and the options must survive the length cap; the file
    # count gives way first, then the step count.
    for optional in ([progress, files], [progress], []):
        text = " ".join([*head, *(o for o in optional if o), *tail])
        if len(text) <= MAX_VOICE_CHARS:
            return text
    return _truncate(text)


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
            # An unknown code speaks English, never German (AP-21: an
            # arbitrary user is not a German speaker).
            other: Lang = "de" if lang == "en" else "en"
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
            verdict = honesty_check.verdict
            # The corrected verdict text exists in de/en only; any other
            # language speaks its own fixed phrase rather than a German one.
            if language == "de":
                override_reason = verdict.summary_de or _localized(_NO_TOOL_CALL, "de")
            elif language == "en":
                override_reason = (
                    verdict.summary or _localized(_NO_TOOL_CALL, "en")
                )
            else:
                override_reason = _localized(_NO_TOOL_CALL, language)
            return self.render_failed(reason=override_reason, language=language)

        template = self._pick("approved", language)
        if not template:
            return ""
        # Insert cap so the final string does not exceed 280 chars
        max_insert = MAX_VOICE_CHARS - len(template) + len("{summary}")
        safe_summary = (summary or _localized(_EMPTY_SUMMARY, language)).strip()
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
            if phrase:
                return _truncate(render_agent_brand(phrase))
            return self.render_crash_recovery(language=language)
        template = self._pick("failed", language)
        if not template:
            return ""
        # Map a known machine reason code to a friendly human phrase so a
        # raw snake_case token is never spoken. Shared with the announcer via
        # FAILURE_REASON_PHRASES. Unmapped reasons fall back to the raw text.
        mapped = FAILURE_REASON_PHRASES.get(language, {}).get(short_reason)
        if mapped:
            mapped = render_agent_brand(mapped)
        safe_reason = mapped if mapped else (
            reason or _localized(_UNKNOWN_REASON, language)
        ).strip()
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
        self, *, target: str = "", language: Lang = "de"
    ) -> str:
        template = self._pick("destructive_confirm", language)
        if not template:
            return ""
        max_insert = MAX_VOICE_CHARS - len(template) + len("{target}")
        safe_target = (target or _localized(_DEFAULT_TARGET, language)).strip()
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
    "approved_summary",
    "TemplateKey",
    "failure_phrase_key",
]
