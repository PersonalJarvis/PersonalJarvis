"""Seed-Workflows — die werden beim ersten Startup in die DB gepflanzt.

Philosophie: **klein, sofort funktionsfaehig, demo-bar.** Wir wollen, dass
der User nach dem ersten Start die WorkflowsView oeffnet und 3 sinnvolle
Beispiele sieht, auf "Run" klicken kann und sofort ein Resultat bekommt.

- *Morgen-Briefing* (cron 30 7 * * *) — brain_prompt → speak-Chain. Produziert
  eine Mini-Standup-Ansage. Kein externer Service noetig.
- *Code-Review* (manual) — harness_dispatch an OpenClaw.
- *URL-Zusammenfassung* (manual, Input-Feld ``url``) — brain_prompt mit
  Template-Variable {{input.url}}. Demoed Input-Binding.
"""
from __future__ import annotations

import logging
import time
from uuid import UUID

from .schema import (
    BrainPromptStep,
    CronTrigger,
    HarnessDispatchStep,
    ManualTrigger,
    ShellCmdStep,
    SpeakStep,
    TelegramSendStep,
    WorkflowDef,
)
from .store import WorkflowStore

log = logging.getLogger(__name__)


# Fixe UUIDs, damit wiederholtes Seeding idempotent ist — wir erkennen
# bestehende Seed-Eintraege an der ID und lassen User-Modifikationen
# ueberleben (kein Force-Overwrite).
_WF_MORGEN_BRIEFING = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000001")
_WF_CODE_REVIEW = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000002")
_WF_URL_SUMMARY = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000003")
_WF_EMAIL_DIGEST = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000004")
_WF_GIT_STANDUP = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000005")


def _morgen_briefing() -> WorkflowDef:
    now_ns = time.time_ns()
    return WorkflowDef(
        id=_WF_MORGEN_BRIEFING,
        name="Morgen-Briefing",
        description=(
            "Taegliche 7:30-Ansage: Aktuelle Uhrzeit, Wochentag und eine kurze, "
            "freundliche Begruessung. Demonstriert brain_prompt → speak-Chain."
        ),
        trigger=CronTrigger(expression="30 7 * * *"),
        steps=(
            BrainPromptStep(
                label="Tages-Zusammenfassung generieren",
                prompt=(
                    "Du bist Jarvis. Es ist jetzt Morgen. Formuliere eine kurze, "
                    "freundliche Morgen-Ansage (max 3 Saetze, Deutsch). Beziehe "
                    "Wochentag und eine motivierende Randbemerkung ein. KEINE "
                    "Emojis, KEINE Uhrzeit-Nennung — der User sieht sie eh."
                ),
                max_output_chars=500,
            ),
            SpeakStep(
                label="Ansage abspielen",
                text="{{prev.output}}",
                priority="normal",
                language="de",
            ),
        ),
        enabled=False,  # Cron-Default off — User soll bewusst aktivieren
        created_at_ns=now_ns,
        created_by="seed",
        tags=("demo", "brain", "speak"),
    )


def _code_review() -> WorkflowDef:
    now_ns = time.time_ns()
    return WorkflowDef(
        id=_WF_CODE_REVIEW,
        name="Code-Review",
        description=(
            "Analysiert die offenen Changes auf dem aktuellen Git-Branch via "
            "OpenClaw-Harness."
        ),
        trigger=ManualTrigger(),
        steps=(
            HarnessDispatchStep(
                label="OpenClaw dispatch",
                harness="openclaw",
                prompt=(
                    "Review all pending changes on the current git branch. "
                    "Identify potential bugs, security issues, or style "
                    "inconsistencies. Return a bullet-point summary in German."
                ),
                allow_computer_use=False,
            ),
            SpeakStep(
                label="Review-Resultat ansagen",
                text="Code-Review fertig. {{prev.output}}",
                priority="normal",
                language="de",
            ),
        ),
        enabled=True,
        created_at_ns=now_ns,
        created_by="seed",
        tags=("demo", "harness", "git"),
    )


def _url_summary() -> WorkflowDef:
    now_ns = time.time_ns()
    return WorkflowDef(
        id=_WF_URL_SUMMARY,
        name="URL-Zusammenfassung",
        description=(
            "Nimmt eine URL als Input, laesst den Brain eine kurze Analyse "
            "erzeugen (KEIN echter Fetch — der Brain kommentiert was er aus "
            "der URL schliessen kann). Demoed Input-Binding via {{input.url}}."
        ),
        trigger=ManualTrigger(),
        steps=(
            BrainPromptStep(
                label="URL analysieren",
                prompt=(
                    "Der User moechte die folgende URL zusammengefasst haben: "
                    "{{input.url}}\n\n"
                    "Erklaere in 3-5 Saetzen auf Deutsch, welche Art von Seite "
                    "das vermutlich ist (Domain-Analyse, Pfad-Heuristik). Wenn "
                    "die URL leer ist, sage das klar."
                ),
                max_output_chars=1200,
            ),
        ),
        enabled=True,
        created_at_ns=now_ns,
        created_by="seed",
        tags=("demo", "brain", "input"),
    )


def _email_digest_telegram() -> WorkflowDef:
    """Die User-Story aus der Session: 5x am Tag Gmail-Inbox triagen, zu
    einer kompakten Zusammenfassung verdichten, via Telegram pushen.

    Kette:
      1. ``shell_cmd``    → ``gws gmail +triage`` → JSON mit Unread-Mails
      2. ``brain_prompt`` → fasse Mails zu 3-5 Bullet-Points auf Deutsch
      3. ``telegram_send`` → Push an den Default-Chat aus der Config

    ``gws``-CLI ist systemweit installiert + authentifiziert (steht in der
    globalen CLAUDE.md). Telegram-Bot-Token + Chat-ID muss der User einmalig
    konfigurieren; bis dahin bleibt der Workflow disabled.

    Cron ``0 8,11,14,17,20 * * *`` → 8:00, 11:00, 14:00, 17:00, 20:00.
    """
    now_ns = time.time_ns()
    return WorkflowDef(
        id=_WF_EMAIL_DIGEST,
        name="Email-Digest via Telegram",
        description=(
            "5x täglich Gmail-Inbox triagen, KI-Zusammenfassung der "
            "ungelesenen Mails erstellen und per Telegram pushen. "
            "Demonstriert die Gmail+Brain+Telegram-Integration. "
            "Braucht konfigurierten Telegram-Bot — siehe "
            "[integrations.telegram] in jarvis.toml."
        ),
        trigger=CronTrigger(expression="0 8,11,14,17,20 * * *"),
        steps=(
            ShellCmdStep(
                label="Gmail Inbox triagen",
                command="gws gmail +triage",
                timeout_s=30.0,
                max_output_chars=12000,
            ),
            BrainPromptStep(
                label="Mails zusammenfassen",
                prompt=(
                    "Du bekommst die Ausgabe eines Gmail-Triage-Tools. "
                    "Erstelle eine kompakte Zusammenfassung der ungelesenen "
                    "Mails auf Deutsch:\n"
                    "- max. 5 Bullet-Points, sortiert nach Dringlichkeit.\n"
                    "- Jeder Punkt: *Absender*: Betreff (in 1 Satz worum es geht).\n"
                    "- Wenn 0 Mails: nur '✅ Inbox leer' zurückgeben.\n\n"
                    "Rohdaten:\n{{prev.output}}"
                ),
                max_output_chars=2000,
            ),
            TelegramSendStep(
                label="An Telegram pushen",
                text="📬 *Email-Digest*\n\n{{prev.output}}",
            ),
        ),
        enabled=False,  # erst aktivieren wenn Telegram konfiguriert
        created_at_ns=now_ns,
        created_by="seed",
        tags=("demo", "gmail", "telegram", "cron"),
    )


def _git_standup_telegram() -> WorkflowDef:
    """Wochentags 9:00 — Git-Status + commit-Log ueber Telegram an den
    Nutzer pushen. Demoed ``shell_cmd`` mit Input-Variable + Chaining.
    """
    now_ns = time.time_ns()
    return WorkflowDef(
        id=_WF_GIT_STANDUP,
        name="Git-Standup via Telegram",
        description=(
            "Werktags 9:00: Zeigt die letzten 5 Commits im aktuellen "
            "Verzeichnis, lässt den Brain eine Stand-up-taugliche "
            "Zusammenfassung ('was wurde gestern gemacht') schreiben "
            "und schickt sie per Telegram."
        ),
        trigger=CronTrigger(expression="0 9 * * 1-5"),
        steps=(
            ShellCmdStep(
                label="Letzte Commits holen",
                command="git log --since=24.hours --pretty=format:%h_%s",
                timeout_s=10.0,
                max_output_chars=4000,
            ),
            BrainPromptStep(
                label="Standup formulieren",
                prompt=(
                    "Das hier sind die Commits der letzten 24 Stunden:\n"
                    "{{prev.output}}\n\n"
                    "Schreibe eine 3-Satz-Zusammenfassung auf Deutsch im "
                    "Stand-up-Stil (Was hab ich gemacht? Was kommt? "
                    "Blocker?). Wenn keine Commits: sag das kurz und "
                    "freundlich."
                ),
                max_output_chars=1000,
            ),
            TelegramSendStep(
                label="Standup pushen",
                text="🧑‍💻 *Dein Standup*\n\n{{prev.output}}",
            ),
        ),
        enabled=False,
        created_at_ns=now_ns,
        created_by="seed",
        tags=("demo", "git", "telegram", "cron"),
    )


SEED_WORKFLOWS: tuple[WorkflowDef, ...] = (
    _morgen_briefing(),
    _code_review(),
    _url_summary(),
    _email_digest_telegram(),
    _git_standup_telegram(),
)


async def ensure_seed_workflows(store: WorkflowStore) -> int:
    """Pflanzt fehlende Seed-Workflows. Returnt die Anzahl Neuanlagen.

    Idempotent — wenn ein Seed-Workflow (per UUID) bereits existiert, lassen
    wir ihn unberuehrt, auch wenn der User Name/Steps geaendert hat. Das
    verhindert dass Updates am Seed-Code die User-Edits ueberschreiben.
    """
    added = 0
    for wf in SEED_WORKFLOWS:
        existing = await store.get_workflow(str(wf.id))
        if existing is not None:
            continue
        await store.upsert_workflow(wf)
        added += 1
    if added:
        log.info("Seed-Workflows geschrieben: %d neu", added)
    return added
