"""Localized phrases for the deterministic computer-use / local-action paths.

These run OFF the LLM — the local-action fast path and the background
computer-use offload — so the brain's language-pin machinery (which only
governs LLM-generated replies) never touches them. Each phrase is spoken or
displayed VERBATIM, so it must be rendered in the turn's language HERE (live
bug 2026-06-15: an all-English computer-use turn ended with the German
"Erledigt." completion readback).

Pure dict lookups — no LLM, no IO (AP-9 / AP-11). Languages: de / en / es;
an unknown language falls back to German (the historical default). The German
column is intentionally the same wording the paths used before, so a German
turn is byte-identical to the old behavior.
"""
from __future__ import annotations

import re

from jarvis.core.turn_language import resolve_turn_language

_DEFAULT = "de"
_SUPPORTED = ("de", "en", "es")

#: ``HarnessTask.env`` key carrying the turn's resolved output language (de/en/es)
#: into the computer-use harness, so the in-harness verifier writes its spoken
#: ``proof`` in the user's language instead of defaulting to English (live bug
#: 2026-06-27: a German turn read back "Erledigt — The file explorer window is
#: open ..."). Set by the producers (``computer_use`` tool + local-action gate),
#: read by ``jarvis.harness.screenshot_only_loop`` — shared here so the three
#: sites cannot drift apart.
OUTPUT_LANGUAGE_ENV_KEY = "JARVIS_OUTPUT_LANGUAGE"

#: ``AnnouncementRequested.source_layer`` marker the router-tier ``computer_use``
#: TOOL stamps on its background-mission completion. The tool runs in its own
#: module with no access to ``BrainManager._history``, so it tags the outcome and
#: the manager mirrors it into the live conversation history (a desktop action
#: picked by the router / in text-chat must be grounded for the next turn too —
#: the same fix the voice fast-path gets inline). Shared here so the producer
#: (computer_use tool) and the consumer (BrainManager subscriber) cannot drift.
CU_TOOL_OUTCOME_LAYER = "brain.computer_use_tool"

# key -> {lang -> template}. Templates may carry named ``{fields}``.
_PHRASES: dict[str, dict[str, str]] = {
    # Computer-use background offload — outcome readbacks (announcement bus).
    "cu_done": {
        "de": "Erledigt.",  # i18n-allow
        "en": "Done.",
        "es": "Listo.",
    },
    # Success WITH the verifier's on-screen observation forwarded (the SUCCESS
    # sibling of cu_failed_reason). Lets an informational request actually be
    # answered — "open the browser and check my tabs" reads back the observed
    # tab instead of a content-free "Done." (live bug 2026-06-18, session
    # 241a1984). ``{detail}`` is the verifier's own sentence (already in the
    # turn's observed language, same as the failure-reason forwarding path).
    "cu_done_detail": {
        "de": "Erledigt. {detail}",  # i18n-allow
        "en": "Done. {detail}",
        "es": "Listo. {detail}",
    },
    "cu_failed": {
        "de": "Das am Bildschirm hat nicht geklappt.",  # i18n-allow
        "en": "That didn't work on screen.",
        "es": "Eso no funcionó en la pantalla.",
    },
    "cu_failed_reason": {
        "de": "Das am Bildschirm hat nicht geklappt: {error}",  # i18n-allow
        "en": "That didn't work on screen: {error}",
        "es": "Eso no funcionó en la pantalla: {error}",
    },
    "cu_crashed": {
        "de": "Beim Erledigen am Bildschirm ist leider etwas schiefgegangen.",  # i18n-allow
        "en": "Something went wrong while doing it on screen.",
        "es": "Algo salió mal al hacerlo en la pantalla.",
    },
    # Computer-use failure readbacks keyed off the harness EXIT CODE. These are
    # plain-language sentences for the case where the underlying layer gives us
    # only an opaque "exit N" token (no human reason). They exist so the user
    # NEVER hears a raw exit code — live bug: the spoken readback was "That
    # didn't work on screen: exit 5" and the user asked "what is the exit file?".
    # Exit-code semantics are documented at the top of
    # jarvis/harness/screenshot_only_loop.py (5=`fail`, 1=observe, 2=parse,
    # 3=no vision-capable provider reachable, 4=step budget, 8=tool failure,
    # 9=elevation unmet, 124=timeout, 130=cancel).
    "cu_exit_gave_up": {  # exit 5 — the model's `fail` action
        "de": "Ich habe es am Bildschirm versucht, aber nicht hinbekommen.",  # i18n-allow
        "en": "I tried it on screen but couldn't get it done.",
        "es": "Lo intenté en la pantalla, pero no pude completarlo.",
    },
    "cu_exit_no_view": {  # exit 1 — observe failure (no usable screenshot)
        "de": "Ich konnte den Bildschirm nicht richtig sehen und habe abgebrochen.",  # i18n-allow
        "en": "I couldn't see the screen properly, so I stopped.",
        "es": "No pude ver bien la pantalla, así que lo detuve.",
    },
    "cu_exit_confused": {  # exit 2 — invalid model / parse response
        "de": "Ich konnte keine gueltige Bildschirm-Antwort bekommen und habe gestoppt.",  # i18n-allow
        "en": "I couldn't get a valid screen-control response, so I stopped.",
        "es": "No pude obtener una respuesta valida para controlar la pantalla, asi que me detuve.",
    },
    # exit 3 — the vision-provider chain was EXHAUSTED: no screen-capable AI model
    # was reachable (every candidate keyless / out-of-credit / rate-limited /
    # no-vision). This is NOT the model "getting confused" (exit 2); it is an
    # account/credential fact the user can fix in-app, so the phrase is honest and
    # ACTIONABLE (points at keys/credit/settings) instead of the misleading
    # "couldn't get a valid response". Live forensic 2026-06-30: the user's whole
    # CU chain was dead (Gemini credits depleted, Claude 502, OpenAI no key,
    # OpenRouter model no-vision) yet they heard the generic exit-2 phrase.
    "cu_exit_no_provider": {  # exit 3 — no vision-capable provider reachable
        "de": "Ich habe gerade kein KI-Modell, das den Bildschirm sehen kann. "  # i18n-allow
              "Bitte pruefe deine API-Schluessel oder dein Guthaben "  # i18n-allow
              "in den Einstellungen.",  # i18n-allow
        "en": "I don't have an AI model that can see the screen right now. "
              "Please check your API keys or your credit in settings.",
        "es": "Ahora mismo no tengo un modelo de IA que pueda ver la pantalla. "
              "Revisa tus claves de API o tu saldo en los ajustes.",
    },
    "cu_exit_too_many_steps": {  # exit 4 — step budget exhausted
        "de": "Es hat am Bildschirm zu viele Schritte gebraucht, ich habe "  # i18n-allow
              "aufgehoert.",  # i18n-allow
        "en": "It took too many steps on screen, so I stopped.",
        "es": "Hicieron falta demasiados pasos en la pantalla, así que lo detuve.",
    },
    "cu_exit_action_failed": {  # exit 8 — tool/action failure
        "de": "Eine Aktion am Bildschirm ist fehlgeschlagen, ich habe abgebrochen.",  # i18n-allow
        "en": "An action on screen failed, so I stopped.",
        "es": "Una acción en la pantalla falló, así que lo detuve.",
    },
    "cu_exit_cancelled": {  # exit 130 — cancel token (e.g. voice hangup)
        "de": "Die Aktion am Bildschirm wurde abgebrochen.",  # i18n-allow
        "en": "The action on screen was cancelled.",
        "es": "La acción en la pantalla se canceló.",
    },
    "cu_exit_needs_elevation": {  # exit 9 — waited for admin confirmation, none came
        "de": "Ich habe auf die Administrator-Bestaetigung gewartet, aber es kam "  # i18n-allow
              "keine, also habe ich gestoppt.",  # i18n-allow
        "en": "I waited for the administrator confirmation, but none came, so I stopped.",
        "es": "Esperé la confirmación de administrador, pero no llegó, así que me detuve.",
    },
    "cu_timeout": {
        "de": "Das am Bildschirm hat zu lange gedauert "  # i18n-allow
              "(ueber {secs} Sekunden) und wurde abgebrochen.",  # i18n-allow
        "en": "That took too long on screen (over {secs} seconds) and was cancelled.",
        "es": "Eso tardó demasiado en la pantalla (más de {secs} segundos) y se canceló.",
    },
    # Computer-use dispatch — the immediate optimistic ACK.
    "cu_dispatch_ack": {
        "de": "Mach ich. Ich erledige das direkt am Bildschirm "  # i18n-allow
              "und sage Bescheid, sobald es fertig ist.",  # i18n-allow
        "en": "On it. I'll handle that on screen and let you know when it's done.",
        "es": "Voy. Lo hago directamente en la pantalla y te aviso cuando termine.",
    },
    # Computer-use pause: an OS elevation prompt (Windows Secure Desktop & co.)
    # is up. A non-elevated process can neither see nor click it (UIPI), so we
    # ask the user for the one unavoidable confirmation click and then continue.
    # Phrased OS-neutrally ("security prompt") — the Windows path is the only one
    # that triggers it today, but macOS/Linux detection may join later.
    "cu_awaiting_elevation": {
        "de": "Das verlangt gerade Administrator-Rechte. Bitte bestaetige die "  # i18n-allow
              "Sicherheitsabfrage einmal, dann mache ich weiter.",  # i18n-allow
        "en": "This needs administrator rights. Please confirm the security "
              "prompt once, then I'll keep going.",
        "es": "Esto requiere permisos de administrador. Confirma el aviso de "
              "seguridad una vez y continúo.",
    },
    # Computer-use human handoff: a screen only the USER may complete is up — a
    # login / password entry, a 2FA / one-time-code prompt, or a CAPTCHA. The
    # agent holds no secrets (AP-2) and must never type them, so it asks the user
    # to take over, polls until the screen clears, then resumes. Phrased generally
    # so it fits login, code-entry and captcha alike.
    "cu_awaiting_human": {
        "de": "Dieser Schritt braucht dich, bitte melde dich an oder loese "  # i18n-allow
              "die Bestaetigung, dann mache ich weiter.",  # i18n-allow
        "en": "This step needs you, please sign in or complete the "
              "verification, then I'll keep going.",
        "es": "Este paso te necesita, inicia sesión o completa la "
              "verificación y continúo.",
    },
    # Cost / budget guards on the computer-use branch.
    "cost_cooldown": {
        "de": "Cost-Cooldown aktiv, das Tagesbudget ist erschoepft. "  # i18n-allow
              "Neue Anfragen werden erst nach dem Cooldown-Ende bearbeitet.",  # i18n-allow
        "en": "Cost cooldown active, the daily budget is used up. "
              "New requests resume once the cooldown ends.",
        "es": "Enfriamiento de costes activo, el presupuesto diario se agotó. "
              "Las nuevas solicitudes se reanudan al terminar el enfriamiento.",
    },
    "task_budget": {
        "de": "Task-Budget fuer diese Konversation ueberschritten.",  # i18n-allow
        "en": "The task budget for this conversation is exceeded.",
        "es": "Se superó el presupuesto de tareas de esta conversación.",
    },
    "daily_budget": {
        "de": "Tagesbudget ueberschritten.",  # i18n-allow
        "en": "The daily budget is exceeded.",
        "es": "Se superó el presupuesto diario.",
    },
    # Direct local-action tool failure fallback.
    "tool_failed": {
        "de": "{tool} fehlgeschlagen.",  # i18n-allow
        "en": "{tool} failed.",
        "es": "{tool} falló.",
    },
    # Generic action-failure fallback — the deterministic line behind the
    # context-aware composer for ANY failed action (local tool, recovered tool).
    # The user must NEVER hear the opaque ``exit N`` token the tool layer emits;
    # when there is no human reason to forward, this honest, plain sentence is
    # spoken instead (live forensic 2026-06-28: a harness failure was read back
    # as the bare "exit 1"). All supported languages, never a hardcoded German
    # string on an en/es turn.
    "action_failed_generic": {
        "de": "Das hat gerade nicht geklappt.",  # i18n-allow
        "en": "That didn't work just now.",
        "es": "Eso no funcionó ahora mismo.",
    },
    # Action failure WITH a human reason forwarded (the speakable cause pulled
    # from the tool's stderr/error — never a bare exit code).
    "action_failed_reason": {
        "de": "Das hat nicht geklappt: {reason}",  # i18n-allow
        "en": "That didn't work: {reason}",
        "es": "Eso no funcionó: {reason}",
    },
    # A local action that ran past its short deadline. Replaces the old
    # tool-name-prefixed "X timeout after 3s" machine string (which leaked the
    # internal tool name) with a plain, honest sentence.
    "action_timeout": {
        "de": "Das hat zu lange gedauert, also habe ich abgebrochen.",  # i18n-allow
        "en": "That took too long, so I stopped.",
        "es": "Eso tardó demasiado, así que lo detuve.",
    },
    # Background sub-agent / worker could not be started. The spoken sibling of
    # action_failed_* for the spawn paths — the opaque ``exit N`` / a hardcoded
    # German fallback must never reach an en/es turn.
    "spawn_failed_generic": {
        "de": "Ich konnte den Hintergrund-Helfer gerade nicht starten.",  # i18n-allow
        "en": "I couldn't start the background helper just now.",
        "es": "No pude iniciar el ayudante en segundo plano ahora mismo.",
    },
    "spawn_failed_reason": {
        "de": "Ich konnte den Hintergrund-Helfer nicht starten: {reason}",  # i18n-allow
        "en": "I couldn't start the background helper: {reason}",
        "es": "No pude iniciar el ayudante en segundo plano: {reason}",
    },
    # Deterministic wiki-write fast path (spec A1-A3). The saving line is a
    # PROGRESS ack — it must never claim the write already happened; the
    # saved/failed lines are the post-write truth (confirm-after-write).
    "wiki_saving": {
        "de": "Ich schreibe das jetzt ins Wiki.",  # i18n-allow
        "en": "Writing that to the wiki now.",
        "es": "Lo estoy escribiendo en la wiki.",
    },
    "wiki_saved": {
        "de": "Im Wiki gespeichert.",  # i18n-allow
        "en": "Saved to the wiki.",
        "es": "Guardado en la wiki.",
    },
    "wiki_saved_detail": {
        "de": "Im Wiki gespeichert: {detail}.",  # i18n-allow
        "en": "Saved to the wiki: {detail}.",
        "es": "Guardado en la wiki: {detail}.",
    },
    "wiki_save_failed": {
        "de": "Das Speichern im Wiki hat nicht geklappt.",  # i18n-allow
        "en": "Saving to the wiki did not work.",
        "es": "No se pudo guardar en la wiki.",
    },
    "wiki_save_failed_reason": {
        "de": "Das Speichern im Wiki hat nicht geklappt: {reason}",  # i18n-allow
        "en": "Saving to the wiki did not work: {reason}",
        "es": "No se pudo guardar en la wiki: {reason}",
    },
    "wiki_nothing_to_save": {
        "de": "Mir ist nicht klar, was ich ins Wiki schreiben soll. Sag es mir "  # i18n-allow
              "bitte noch einmal mit Inhalt.",  # i18n-allow
        "en": "I am not sure what to write to the wiki. Please say it again with the content.",
        "es": "No tengo claro qué escribir en la wiki; dímelo otra vez con el contenido.",
    },
}


def resolve_phrase_language(reply_language: str | None, user_text: str) -> str:
    """Resolve de/en/es for a deterministic action phrase.

    An explicit ``brain.reply_language`` pin (de/en/es) wins; otherwise the
    turn's language is detected from ``user_text``; ambiguous text keeps the
    historical German default. Mirrors ``BrainManager._direct_ack_language`` so
    every non-LLM spoken path resolves language the same way.
    """
    if reply_language in _SUPPORTED:
        return str(reply_language)
    return resolve_turn_language("unknown", user_text, default=_DEFAULT)


def action_phrase(key: str, lang: str, **fmt: object) -> str:
    """Render the localized phrase ``key`` in ``lang`` (de/en/es).

    Unknown languages fall back to German. ``fmt`` fills any named template
    fields (e.g. ``error``, ``secs``, ``tool``).
    """
    variants = _PHRASES[key]
    template = variants.get(lang) or variants[_DEFAULT]
    return template.format(**fmt) if fmt else template


# A bare "exit N" token, optionally wrapped in brackets/whitespace, with nothing
# else of substance around it — the opaque string ``dispatch_to_harness`` emits
# (``f"exit {exit_code}"``). This must never be spoken to the user.
_BARE_EXIT_RE = re.compile(r"^\s*\(?\s*exit\s*\d+\s*\)?\s*$", re.IGNORECASE)
#: The harness CANCEL exit code (128 + SIGINT). Emitted ONLY when the
#: computer-use cancel token is tripped — i.e. the user hung up ("auflegen").
#: Mirrors ``_CANCEL_EXIT_CODE`` in jarvis/harness/screenshot_only_loop.py.
#: Exposed publicly so the background-offload path can recognise a
#: user-initiated abort and stay SILENT: the user just triggered the abort
#: themselves, so a "the action was cancelled" readback is redundant — and with
#: N parallel offloaded missions it would spam the phrase N times (live forensic
#: 2026-06-27). "auflegen" is a hard, immediately-silent kill-switch.
CU_CANCEL_EXIT_CODE: int = 130
#: Static map: harness exit code -> generic plain-language phrase key. Keep in
#: sync with the exit-code legend in jarvis/harness/screenshot_only_loop.py.
_EXIT_CODE_PHRASE: dict[int, str] = {
    1: "cu_exit_no_view",
    2: "cu_exit_confused",
    3: "cu_exit_no_provider",
    4: "cu_exit_too_many_steps",
    5: "cu_exit_gave_up",
    8: "cu_exit_action_failed",
    9: "cu_exit_needs_elevation",
    CU_CANCEL_EXIT_CODE: "cu_exit_cancelled",
}
#: Strip the loop's "[cu] <verb> at <tag>: " prefix so the human reason the
#: model gave for ``fail`` surfaces clean (the loop writes
#: ``"[cu] fail at step-N: <reason>"`` to stderr).
_CU_REASON_PREFIX_RE = re.compile(
    r"^\s*\[cu\][^:]*:\s*", re.IGNORECASE,
)
#: Internal computer-use DIAGNOSTIC / telemetry markers. A stderr fragment
#: carrying any of these is loop instrumentation — the no-progress guard, the
#: anti-oscillation / toggle guards, or the latency "mission profile" summary —
#: NOT the model's human ``fail`` reason. It must never be forwarded into a
#: spoken/displayed readback. Live bug 2026-06-20 (Angela-Merkel x.com mission):
#: the user heard "Das am Bildschirm hat nicht geklappt: 3 identical screenshots  # i18n-allow  # i18n-allow
#: in a row at step 9 -- the click ..." followed by the raw
#: "[cu] mission profile: steps=9 ..." telemetry line. The exit-code phrases
#: (``cu_exit_*``) exist precisely so these cases degrade to a plain sentence.
_CU_DIAGNOSTIC_RE = re.compile(
    r"\[cu\]"                      # any engineering-prefixed loop line
    r"|identical\s+screenshots?"   # no-progress guard
    r"|mission\s+profile"          # latency telemetry summary
    r"|guard-blocked"              # anti-oscillation / modal guards
    r"|toggle-stop",               # repeated-click toggle guard
    re.IGNORECASE,
)
#: A raw VERIFIER OBSERVATION dump, as opposed to a clean user-facing answer.
#: The read-goal verifier (``_READ_VERIFIER_SYSTEM_PROMPT`` in
#: ``screenshot_only_loop.py``) is a STRICT completion judge: it often PROVES the
#: goal by describing the UI structurally ("Foreground window (...): title '...',
#: content starts '...', followed by bullet points ..., Bottom status bar: ...")
#: rather than answering the user in plain language. Such a proof is an internal
#: evidence artifact (the spirit of ADR-0009 — the voice readback speaks only a
#: clean summary, never the raw signed observation), so it must never be spoken
#: verbatim. Live bug 2026-06-21 (travel-guide HTML turn): a "look at my screen
#: and tell me which app is open" mission read out the full structural dump,
#: INCLUDING the verbatim content of an unrelated parallel Claude-Code
#: AskUserQuestion box ("Eine Detail-Erkenntnis ... Der Guide rendert ...").  # i18n-allow
#: A genuine
#: content answer ("the browser is open showing tab 'Gmail'", "newest messages:
#: Alice 'hi'") carries none of these structural markers and is still forwarded.
_CU_VERIFIER_DUMP_RE = re.compile(
    r"\b(?:fore|back)ground\s+window\b"        # window-layer vocabulary
    r"|\bactive\s+window\b"
    r"|\bvordergrundfenster\b"                  # i18n-allow: DE verifier
    r"|\b(?:status|title|menu|tool|task|side|scroll|address)[\s-]?bars?\b"
    r"|(?:status|titel|men[üu]|symbol|seiten|bildlauf|adress|task|werkzeug)"  # i18n-allow
    r"[\s-]?leisten?\b"                         # i18n-allow: Status-/Titel-/Symbolleiste
    r"|\bcontent\s+(?:starts?|reads?|begins?|shows?|says?)\b"
    r"|\bfollowed\s+by\s+bullet"
    r"|\bbullet\s+points?\b"
    r"|\bwindow\s+(?:title|content|titled)\b",
    re.IGNORECASE,
)
#: The latency "mission profile" line is a run of numeric ``key=value`` telemetry
#: tokens: "steps=3 total=9.5s act=3.0s observe=0.3s plan=1.6s think=4.6s". The
#: FAILURE path strips the "[cu] mission profile:" PREFIX (via
#: ``_CU_REASON_PREFIX_RE``) before the speakability gate runs, which deletes the
#: very "[cu]"/"mission profile" markers ``_CU_DIAGNOSTIC_RE`` keys on — so the
#: BARE profile sailed straight through and was spoken (live bug 2026-06-22, the
#: Ed-Sheeran "Perfect" turn: 'That didn't work on screen: steps=3 total=9.5s
#: act=3.0s observe=0.3s plan=1.6s think=4.6s'). Detect the profile STRUCTURALLY
#: instead — independent of any prefix: any of the named phase keys, or two or
#: more numeric ``key=value`` tokens in a row, is a machine stats dump, never a
#: human sentence. A lone "HTTP 403" / "2 attempts" carries no ``=`` and survives.
_CU_TELEMETRY_RE = re.compile(
    r"\b(?:steps|total|act|observe|plan|think|verify)=\d"  # a named profile stat
    r"|(?:\b[A-Za-z_][\w-]*=\d[\d.]*\w*\b\s*){2,}",        # >=2 numeric key=value
    re.IGNORECASE,
)
#: A filesystem path or screenshot-artifact filename that leaked into a readback.
#: The harness temp capture ("C:\\…\\pythonw_xxxx.png") rode the failure ``detail``
#: into the spoken text on the same 2026-06-22 turn. A spoken answer never names a
#: path or an image file, so any of these forces the generic phrase. The image
#: extension is matched cross-platform (the screenshot leak is identical on a
#: headless Linux VPS, where the path would be POSIX).
_CU_PATH_ARTIFACT_RE = re.compile(
    r"[A-Za-z]:[\\/]"                                  # a Windows drive-letter path
    r"|\b[\w-]+\.(?:png|jpe?g|gif|bmp|webp|tiff?)\b",  # a screenshot / image file
    re.IGNORECASE,
)


def _is_diagnostic_noise(text: str) -> bool:
    """True if ``text`` is internal machine noise that must never be spoken.

    The shared, structural junk-detector behind both speakability gates. Covers
    the named loop markers (``_CU_DIAGNOSTIC_RE``), the bare latency ``mission
    profile`` stats run (``_CU_TELEMETRY_RE`` — matched by SHAPE so it still trips
    after the "[cu] mission profile:" prefix has been stripped off), and a leaked
    filesystem / screenshot path (``_CU_PATH_ARTIFACT_RE``). Pure regex, no LLM
    (AP-11): the answer is "is this a sentence a butler would say, or a developer
    log line?" — the user's "if you see that junk, you know not to speak it".
    """
    return bool(
        _CU_DIAGNOSTIC_RE.search(text)
        or _CU_TELEMETRY_RE.search(text)
        or _CU_PATH_ARTIFACT_RE.search(text)
    )


def _looks_human(text: str) -> bool:
    """True if ``text`` is a real reason sentence, not an opaque exit token.

    Defensive against the upstream layer: when a parallel change makes the
    harness forward the model's ``fail`` reason, we forward it; only a bare
    ``exit N`` / purely-numeric / empty error gets replaced by a generic phrase.
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    if _BARE_EXIT_RE.match(stripped):
        return False
    # A token that is only digits / punctuation carries no human meaning.
    if not re.search(r"[A-Za-zÀ-ÿ]", stripped):
        return False
    return True


def _is_speakable_reason(text: str | None) -> bool:
    """True if ``text`` is a real, user-facing reason — safe to forward verbatim.

    Stricter than :func:`_looks_human`: besides rejecting bare ``exit N`` /
    numeric / empty tokens, it rejects internal computer-use DIAGNOSTIC and
    telemetry strings (the no-progress guard, the anti-oscillation / toggle
    guards, the bare latency ``mission profile`` stats run, and a leaked
    screenshot path). Those are developer instrumentation written to the harness
    ``stderr`` — never the model's human ``fail`` reason — so they must never
    reach a spoken/displayed readback. A blocked string degrades to the generic,
    localized exit-code phrase instead.
    """
    if not _looks_human(text or ""):
        return False
    return not _is_diagnostic_noise(text or "")


def _is_speakable_observation(text: str | None) -> bool:
    """True if ``text`` is a clean, user-facing observation — safe to speak.

    The SUCCESS sibling of :func:`_is_speakable_reason`. Besides rejecting bare
    ``exit N`` / numeric / empty tokens, internal diagnostic markers, the bare
    telemetry profile, and a leaked screenshot path (all via
    :func:`_is_diagnostic_noise`), it also rejects a raw VERIFIER UI-STRUCTURE
    dump (``_CU_VERIFIER_DUMP_RE``): a "Foreground window (...): title '...',
    content starts '...'" proof is the judge's internal evidence, not the answer
    the user asked for, and leaks whatever happened to be on screen (incl.
    unrelated windows). A blocked observation degrades to the generic, localized
    ``cu_done`` phrase instead.
    """
    if not _looks_human(text or ""):
        return False
    if _is_diagnostic_noise(text or ""):
        return False
    return _CU_VERIFIER_DUMP_RE.search(text or "") is None


def extract_speakable_reason(error: str | None, output: object = None) -> str | None:
    """Pull a clean, user-facing failure reason out of a tool result, or ``None``.

    The single opaque-token gate for every NON-computer-use failure readback
    (the local-action fast path, the spawn / leaked-tool recovery paths). It
    mirrors :func:`cu_failure_readback`'s resolution, reusing the same guards so
    the "what may be spoken" rule lives in ONE place:

    1. If ``output`` is a harness/tool result dict, prefer a human sentence from
       its ``stderr`` (then ``stdout``) — the loop's ``"[cu] … : "`` prefix is
       stripped first.
    2. Otherwise fall back to ``error`` if it is itself a human sentence.
    3. Return ``None`` when nothing speakable remains — a bare ``exit N``, a
       purely numeric / empty token, internal diagnostic / telemetry noise, or a
       leaked path. The caller then degrades to a generic localized phrase.

    Pure regex, no LLM (AP-11). The user must NEVER hear the opaque ``exit N``
    token the tool layer emits (``dispatch_to_harness`` → ``f"exit {code}"``).
    """
    if isinstance(output, dict):
        for field in ("stderr", "stdout"):
            raw = str(output.get(field) or "").strip()
            if not raw:
                continue
            candidate = _CU_REASON_PREFIX_RE.sub("", raw).strip()
            if _is_speakable_reason(candidate):
                return candidate
    if _is_speakable_reason(error):
        return str(error).strip()
    return None


def cu_failure_readback(
    lang: str,
    *,
    error: str | None,
    exit_code: int | None = None,
    detail: str | None = None,
) -> str:
    """Compose the spoken/chat readback for a Computer-Use FAILURE.

    The user must NEVER hear a raw exit code (live bug: "That didn't work on
    screen: exit 5"). This is a pure STATIC lookup — no LLM call (AP-11).

    Resolution order, most-specific first:

    1. ``detail`` (e.g. the harness ``stderr`` carrying the model's ``fail``
       reason) — if it contains a human sentence, FORWARD it (it gets scrubbed
       downstream). The loop's ``"[cu] fail at <tag>: "`` prefix is stripped.
    2. ``error`` — if it is already a human sentence (not a bare ``exit N`` /
       numeric token), FORWARD it.
    3. Otherwise substitute the generic, localized phrase keyed off
       ``exit_code`` (``cu_exit_*``), falling back to the plain
       "didn't work on screen" sentence when the code is unknown.
    """
    # 1) The harness detail string may carry the model's verified reason.
    if detail:
        candidate = _CU_REASON_PREFIX_RE.sub("", detail).strip()
        if _is_speakable_reason(candidate):
            return action_phrase("cu_failed_reason", lang, error=candidate)

    # 2) A human reason already on the error field is forwarded verbatim.
    if _is_speakable_reason(error):
        return action_phrase("cu_failed_reason", lang, error=str(error).strip())

    # 3) Opaque / empty error -> map the exit code to a human phrase.
    phrase_key = _EXIT_CODE_PHRASE.get(int(exit_code)) if exit_code is not None else None
    if phrase_key is None:
        # Unknown / missing exit code: the neutral "didn't work on screen".
        return action_phrase("cu_failed", lang)
    return action_phrase(phrase_key, lang)


def _extract_cu_proof(stdout: str) -> str | None:
    """Pull the verifier's observation out of the harness success ``stdout``.

    The screenshot loop writes the proof as ``"[cu] done at <tag> (verified:
    <proof>)"`` (or ``"[cu] done (verified: <proof>)"``). We scan for that line
    and return everything inside ``(verified: ...)`` — taking up to the FINAL
    ``)`` so an inner parenthesis in the proof itself ("Der Browser (Chrome)
    ...") survives. Returns ``None`` when there is no verified-proof line (e.g.
    ``"[cu] done at <tag>"`` with no observation), so the caller can fall back.
    """
    marker = "(verified:"
    for line in (stdout or "").splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if "[cu] done" not in low or marker not in low:
            continue
        start = low.index(marker) + len(marker)
        proof = stripped[start:].strip()
        if proof.endswith(")"):
            proof = proof[:-1].strip()
        return proof or None
    return None


def cu_success_readback(lang: str, *, stdout: str | None) -> str:
    """Compose the spoken/chat readback for a Computer-Use SUCCESS.

    The SUCCESS sibling of :func:`cu_failure_readback`. When the harness
    verified the goal and left a human observation in ``stdout`` (e.g. "the
    browser is open showing tab 'X'"), FORWARD it so an informational request
    is actually answered — the observation is the answer the user asked for. A
    pure STATIC parse + lookup, no LLM call (AP-11); the text is scrubbed
    downstream like every other spoken phrase.

    Falls back to the plain localized ``cu_done`` phrase when the mission left
    no usable observation (no ``(verified: ...)`` segment, an empty/opaque
    proof, or — via :func:`_is_speakable_observation` — a raw verifier UI
    STRUCTURE dump that describes the screen instead of answering the user).
    """
    proof = _extract_cu_proof(stdout or "")
    if proof and _is_speakable_observation(proof):
        return action_phrase("cu_done_detail", lang, detail=proof)
    return action_phrase("cu_done", lang)


__all__ = [
    "CU_CANCEL_EXIT_CODE",
    "CU_TOOL_OUTCOME_LAYER",
    "OUTPUT_LANGUAGE_ENV_KEY",
    "action_phrase",
    "cu_failure_readback",
    "cu_success_readback",
    "extract_speakable_reason",
    "resolve_phrase_language",
]
