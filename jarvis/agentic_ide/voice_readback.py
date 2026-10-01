"""Tell the user, out loud, how a job they gave a pane through Jarvis ended.

"Prompt terminal T1 to fix the login bug" used to end at "Sent — T1 has the
brief and is working on it." The pane then worked for minutes, stopped, and
nothing more was said: the only sign was the bell in the IDE header. A job
the user gave Jarvis is a question Jarvis owes an answer to, so when such a
pane stops, Jarvis now says what it did — or, when it stopped on a question,
what it is asking.

What counts as "given through Jarvis" is decided at the send site
(``Registry.send_prompt(readback=True)``: the voice fan-out and the IDE prompt
bar) and carried on the pane (``Terminal.voice_readback``). A job typed into
the pane by hand, and one a supervising Jarvis agent handed over (it reports
itself), are not announced.

When it is spoken: the announcement uses a source the speech pipeline holds
for an open call (``_HELD_FOR_CALL_SOURCES``) — inside a call it is spoken at
the next free moment, outside one it waits for the next call. Jarvis never
speaks into a room where nobody called it.

What it says comes from the pane's own record: the coding CLI's final message
when its transcript is readable, else the last lines of its screen. That text
is phrased into one short sentence by the readback composer
(:mod:`jarvis.voice.report_readback`); the canned fallback names the pane and
quotes the report's first sentence.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from loguru import logger

#: Where these announcements come from. Listed in the speech pipeline's
#: held-for-call sources, which is what keeps them out of an idle room.
SOURCE_LAYER = "agentic_ide.readback"

#: How many screen lines are read when no transcript is available.
SCREEN_LINES = 24

ReadbackKind = Literal["completed", "needs_input"]
Publisher = Callable[[Any], Awaitable[None]]

#: Deterministic fallbacks, spoken when the composer cannot phrase the report.
_CANNED: dict[str, dict[str, str]] = {
    "completed": {
        "de": "{name} ist fertig: {report}",  # i18n-allow: spoken readback
        "en": "{name} is done: {report}",
        "es": "{name} ha terminado: {report}",
    },
    "completed_bare": {
        "de": "{name} ist mit deinem Auftrag fertig.",  # i18n-allow: spoken readback
        "en": "{name} has finished your task.",
        "es": "{name} ha terminado tu encargo.",
    },
    "needs_input": {
        "de": "{name} hat eine Frage: {report}",  # i18n-allow: spoken readback
        "en": "{name} has a question: {report}",
        "es": "{name} tiene una pregunta: {report}",
    },
    "needs_input_bare": {
        "de": "{name} wartet auf deine Antwort.",  # i18n-allow: spoken readback
        "en": "{name} is waiting for your answer.",
        "es": "{name} espera tu respuesta.",
    },
}

_INSTRUCTIONS: dict[str, str] = {
    "completed": (
        "A coding agent in the terminal named {name} has finished the task the "
        "user gave it through you. Tell the user what it did or found, based "
        "only on its report. Name the terminal. If the report says something "
        "failed or is still open, say so."
    ),
    "needs_input": (
        "A coding agent in the terminal named {name} stopped while working on "
        "the user's task and is waiting for the user. Tell the user briefly what "
        "it is asking or needs, based only on its screen. Name the terminal."
    ),
}

#: Box drawing, block elements and the spinner/prompt glyphs a TUI paints.
_TUI_NOISE_RE = re.compile(r"[─-▟⠀-⣿■-◿❯⏵⏸]+")

_tasks: set[asyncio.Task[None]] = set()


def schedule(kind: ReadbackKind, term: Any, publish: Publisher | None) -> None:
    """Compose and publish the readback for ``term`` in a task of its own."""
    if publish is None:
        logger.debug("Agentic IDE readback for {}: no bus to speak on", getattr(term, "name", "?"))
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # no event loop (a sync test / CLI): nobody to speak to
        logger.debug("Agentic IDE readback for {}: no event loop", getattr(term, "name", "?"))
        return
    task = loop.create_task(readback(kind, term, publish), name="agentic-ide-readback")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def readback(kind: ReadbackKind, term: Any, publish: Publisher) -> None:
    """Tell the user how the pane's Jarvis job ended. Never raises."""
    name = str(getattr(term, "name", "") or "the terminal")
    try:
        request = str(getattr(term, "voice_readback_request", "") or "")
        report = ""
        if kind == "completed":
            report = await asyncio.to_thread(final_report, term)
        screen = "" if report else screen_text(term)
        language = await asyncio.to_thread(_language, request)
        text = await _compose(kind, name, language, request, report, screen)
        if not text.strip():
            return
        from jarvis.core.events import AnnouncementRequested

        result = publish(
            AnnouncementRequested(
                source_layer=SOURCE_LAYER,
                text=text,
                language=language,
                priority="normal",
                # A readback kind: the answer to something the user asked for.
                kind="completion",
                detail=f"agentic_ide pane={name} outcome={kind}",
                # A live voice model reasons over this itself; ``text`` is
                # what classic TTS speaks.
                report=_report_material(name, request, report, screen),
            )
        )
        if inspect.isawaitable(result):
            await result
        logger.info("Agentic IDE readback for {} ({}): {!r}", name, kind, text[:120])
    except Exception:  # noqa: BLE001 - a lost readback must never touch the sweep
        logger.opt(exception=True).warning("Agentic IDE readback for {} failed", name)


async def _compose(
    kind: ReadbackKind, name: str, language: str, request: str, report: str, screen: str
) -> str:
    from jarvis.voice.report_readback import compose_report, plain_excerpt

    excerpt = plain_excerpt(report, max_words=35)

    def canned() -> str:
        key = kind if excerpt else f"{kind}_bare"
        table = _CANNED[key]
        return table.get(language, table["en"]).format(name=name, report=excerpt)

    facts: dict[str, object] = {"terminal": name}
    if request:
        facts["task the user gave"] = request[:400]
    if screen:
        facts["what the terminal shows"] = screen
    return await compose_report(
        instruction=_INSTRUCTIONS[kind].format(name=name),
        language=language,
        canned=canned,
        report=report,
        facts=facts,
    )


def _report_material(name: str, request: str, report: str, screen: str) -> str:
    """Everything the live model gets to think about, labelled."""
    parts = [f"Terminal: {name}"]
    if request:
        parts.append(f"What the user asked for:\n{request}")
    if report:
        parts.append(f"The coding agent's final message:\n{report}")
    elif screen:
        parts.append(f"No transcript is readable; the terminal's screen shows:\n{screen}")
    return "\n\n".join(parts)


def final_report(term: Any) -> str:
    """The coding agent's last message, from its own transcript, or ``""``.

    Only the answer to the newest user message counts: a transcript whose last
    turn is the user's own has not answered yet.

    A pane on a connected computer writes its transcript THERE; the local copy
    stopped at the offload and ends with an older job's answer (#250). Such a
    pane reports ``""`` and the readback falls back to its live screen.
    """
    from . import agent_transcript
    from .session import account_home

    if getattr(term, "computer_id", ""):
        return ""
    handle = getattr(term, "resume", None)
    agent = str(getattr(term, "agent", "") or "")
    if handle is None or not agent_transcript.can_read(agent):
        return ""
    turns = agent_transcript.read(
        agent, handle.id, home=account_home(agent, getattr(term, "account", None))
    )
    if not turns or turns[-1].role != "assistant":
        return ""
    return str(turns[-1].text or "").strip()


def screen_text(term: Any) -> str:
    """The last lines on the pane's screen, without the TUI's drawing."""
    try:
        lines = list(term.transcript.tail(SCREEN_LINES))
    except Exception:  # noqa: BLE001 - no screen is an empty fact, not an error
        logger.opt(exception=True).debug("Agentic IDE readback: screen unreadable")
        return ""
    cleaned = (" ".join(_TUI_NOISE_RE.sub(" ", line).split()) for line in lines)
    return "\n".join(line for line in cleaned if line)[-1500:]


def _language(request: str) -> str:
    """The language to speak in: the user's pin, else the words they used."""
    from jarvis.core.turn_language import DEFAULT_LOCALE, resolve_output_language

    pin = ""
    try:
        from jarvis.core.config import load_config

        pin = str(getattr(load_config().brain, "reply_language", "") or "")
    except Exception:  # noqa: BLE001 - an unreadable config means "no pin"
        logger.opt(exception=True).debug("Agentic IDE readback: reply-language pin unreadable")
    return resolve_output_language(pin, "", request, default=DEFAULT_LOCALE)


__all__ = ["SOURCE_LAYER", "final_report", "readback", "schedule", "screen_text"]
