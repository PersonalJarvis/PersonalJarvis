"""Turn a spoken instruction into a prompt a coding agent can actually work on.

This is the part that makes the Agentic IDE worth having. Speaking at an agent
and speaking at a *transcript relay* are very different products: dictated
words arrive as one unpunctuated run of filler, pronouns and self-corrections
("kannst du mal schnell, also ähm, das Wake-Ding angucken ob da was kaputt
ist"), and pasting that into Claude Code produces a confused agent that starts
by asking what you meant. What the user asked for is that *Jarvis* does the
prompt engineering: it knows the repo, so it turns that sentence into a briefed
task with the relevant files already attached.

The prompt's SHAPE lives in ``prompt_blueprint`` and the guardrails it carries
are chosen by ``task_kind``; this module is the orchestration around them:
which files to offer, what to read of them, which model writes it, and what
happens when any of that is unavailable.

What the composed prompt contains, and why each part earns its place:

* **A briefed task in markdown**, not a rephrased sentence. Opus 5 and Fable 5
  both work best from a complete specification given up front, so the prompt
  states the task, why it matters, the files, the scope bound, and what done
  looks like — dropping any section it cannot ground rather than padding it.
* **File references in the agent's own syntax** (``@path``), inside the Key
  files list with a reason each. Both supported CLIs read ``@`` as "pull this
  file into context", which removes the agent's entire opening round of blind
  searching. Paths come from the workspace file index — never invented, and
  every one is verified to exist before it ships.
* **Real symbol names**, because the writer reads a bounded outline of those
  files first (``code_skeleton``). Without it the prompt can only say "the
  ranking logic" where it could say ``_fuse_ranked()``.
* **The repository's own house rules**, so ``## Done when`` cites the project's
  actual test command instead of inventing a plausible one.

Two-layer construction, because the product must work for a downloader with no
API key at all (§3):

1. **Composed** — one bounded call to ``resolve_quality_brain``. Deliberately
   NOT the full frontier chain: that chain ends in small, fast models so a core
   path never dies, and a prompt written by one of those looks fine while being
   materially worse. Here the output IS the product, so a chain that cannot
   reach a capable model returns nothing and we fall to layer 2 openly.
2. **Deterministic fallback** — the same markdown skeleton filled by regex,
   used when no provider qualifies, the call fails, times out, or comes back
   empty. It is *always* better than the raw transcript, so the feature never
   depends on a model being available.

The composer is honest about which layer produced the result (``composed_by``),
because the readback the user hears should not claim more than happened.

**On latency (deliberate trade, maintainer decision 2026-07-25).** An earlier
change routed this through the router-tier model the voice turn already held,
because a rewrite took 1-3 s there against 7-8 s on the deep tier. That
optimised the wrong axis: the maintainer's instruction was that these prompts
must be accurate about the codebase and "must not be dumb", and chose the
slower, better prompt explicitly. Reading file outlines costs more still. So
the composer takes its time and the bound below is generous — what must never
happen is a silent demotion to a weaker model, because nobody inspects a prompt
that looks fine.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from .file_index import cached_index
from .session import MAX_PROMPT_CHARS, sanitize_prompt

# The composer is not on the voice hot path the way an ack is, but the user is
# waiting to hear "sent to Kai". On timeout the deterministic prompt ships, so
# this bound costs quality, never delivery.
#
# Measured on the live chain: a fast router-tier model answers in 1-3 s, the
# deep tier took 7-8 s for a plain rewrite, and a full brief that reads five
# file outlines and describes the code took 16-22 s. A bound that expires
# mid-composition does not produce a faster good prompt, it produces the regex
# one — which 8 s did routinely and 20 s still did for the longest brief. The
# ceiling is therefore well clear of the measured worst case.
#
# Raised 45 → 90 s when the writer gained a subscription-CLI path (2026-07-26).
# Those brains pay a cold process start before they think at all: measured
# 10-12 s for a trivial prompt and 26.6 s for a real structured brief on the
# fastest model. At 45 s a slower model or a loaded machine would have expired
# routinely, and every expiry buys the regex prompt — never a faster good one.
COMPOSE_TIMEOUT_S = 90.0

# How many files may be attached. Enough to point the agent at a feature's
# surface; few enough that the agent's context is not flooded with guesses.
MAX_FILE_REFERENCES = 5

# Speech artefacts the deterministic layer removes. Matching *input vocabulary*
# in the supported locales — these are the words people actually say while
# thinking, not prose.
_FILLER_RE = re.compile(
    r"\b(?:"
    r"ähm|ähmm|äh|ehm|hmm|halt|eben|einfach\s+mal|also|ne|gell|weißt\s+du|"
    r"verstehst\s+du|sozusagen|quasi|irgendwie|jetzt\s+mal|mal\s+eben|"
    r"um|uh|erm|like|you\s+know|i\s+mean|sort\s+of|kind\s+of|basically|"
    r"este|o\s+sea|pues|bueno"
    r")\b[\s,]*",
    re.IGNORECASE,
)

_POLITENESS_PREFIX_RE = re.compile(
    r"^(?:"
    r"(?:kannst|k[oö]nntest|w[uü]rdest|willst|magst|k[oö]nnen)\s+(?:du|sie)\b\s*"
    r"|(?:could|can|would|will)\s+you\b\s*"
    r"|(?:puedes|podr[ií]as)\b\s*"
    r"|(?:bitte|please|por\s+favor|mal|kurz|schnell|eben|just|quickly)\b[\s,]*"
    r")+",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ComposedPrompt:
    """The prompt that will be typed, plus how it came to be."""

    text: str
    """What gets sent to the agent."""

    files: list[str] = field(default_factory=list)
    """Repo-relative paths referenced with ``@`` in ``text``."""

    composed_by: str = "fallback"
    """``llm`` when a provider wrote it, ``fallback`` when the regex layer did,
    ``raw`` when composition was switched off by the caller."""

    note: str = ""
    """Why the fallback was used, when it was. Empty on the happy path."""


def _clean_speech(text: str) -> str:
    """The instruction with speech artefacts and politeness scaffolding gone."""
    cleaned = _FILLER_RE.sub(" ", text or "")
    cleaned = _POLITENESS_PREFIX_RE.sub("", cleaned.strip())
    # Collapse the "und zwar dass er ..." construction into a plain imperative
    # opening; it is a spoken connector, never part of the task.
    cleaned = re.sub(
        r"^\s*(?:und\s+zwar\s+)?(?:dass|damit|that|que)\s+(?:er|sie|es|it|he|she|they)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return " ".join(cleaned.split())


def _existing(root: str, candidates: list[str]) -> list[str]:
    """Candidate paths that really exist under ``root``, in the given order.

    A hallucinated ``@path`` is worse than none: the agent opens it, fails, and
    spends a turn recovering. The index can also be stale (a file was renamed
    after the walk), so existence is re-checked here rather than assumed.
    """
    base = Path(root)
    out: list[str] = []
    for rel in candidates:
        # Reject anything trying to leave the workspace — a composed path is
        # model output on the happy path, and it only ever legitimately points
        # inside the folder the user opened.
        if rel.startswith(("/", "\\")) or ".." in rel.split("/") or ":" in rel[:3]:
            continue
        try:
            if (base / rel).is_file():
                out.append(rel)
        except OSError:
            continue
    return out


def _file_candidates(session, instruction: str, limit: int) -> list[str]:  # noqa: ANN001
    """Repo-relative files the instruction plausibly points at."""
    index = cached_index(session.folder)
    if index is None:
        # Still building (or never primed): ship without references rather than
        # make the user wait for a directory walk.
        return []
    return _existing(session.folder, index.suggest(instruction, limit=limit))


def _extract_referenced(text: str) -> list[str]:
    """``@path`` tokens in a composed prompt, in order of appearance."""
    seen: list[str] = []
    for match in re.finditer(r"@([\w./\\-]+)", text or ""):
        rel = match.group(1).replace("\\", "/")
        if rel not in seen:
            seen.append(rel)
    return seen


def _resolve_writer():  # noqa: ANN202 - Brain | None, avoid an import cycle
    """The model that writes prompts, or None when nothing qualifies.

    Delegates to ``writer.resolve_writer`` so this decision lives in ONE place:
    the work splitter makes the same choice, and a user who moved briefs onto a
    subscription must not find the split still billing an API key.

    Neither rung of that order is the full frontier chain. That chain is built
    so a core path never dies and therefore ends in small, fast models; here the
    OUTPUT is the product, and a brief written by a mini model is worse in a way
    nobody notices — it reads perfectly well. Returning None lets the caller
    degrade to the deterministic prompt and say so.
    """
    from .writer import resolve_writer

    brain, _source = resolve_writer(cli_timeout_s=COMPOSE_TIMEOUT_S)
    return brain


# How much of the repository's agent instructions to carry. Enough for the
# headline conventions; far short of pasting a whole CLAUDE.md into every call.
_HOUSE_RULES_CHARS = 1200


def _house_rules(session) -> str:  # noqa: ANN001 - Session, avoid an import cycle
    """The repository's own conventions, so ``## Done when`` stays grounded.

    Without this the writer has to guess a test command, and a guessed one is
    exactly the kind of invented acceptance criterion the blueprint forbids.
    """
    from .code_skeleton import skeleton_for

    names = list(getattr(session.profile, "instruction_files", None) or [])
    for name in names:
        text = skeleton_for(session.folder, name, max_chars=_HOUSE_RULES_CHARS)
        if text:
            return f"From {name}:\n{text}"
    return ""


async def _llm_compose(
    *,
    brain,  # noqa: ANN001 - Brain, avoid an import cycle
    system_prompt: str,
    user_block: str,
) -> str:
    """One bounded call that writes the prompt."""
    from jarvis.core.protocols import BrainMessage, BrainRequest

    request = BrainRequest(
        messages=(BrainMessage(role="user", content=user_block),),
        system=system_prompt,
        # Deterministic rewriting, not creative writing: the prompt must carry
        # the user's intent, so temperature stays low.
        temperature=0.2,
        # Generous, because on a thinking model max_tokens covers the THINKING
        # as well as the answer. Measured: at 3000 with medium effort, a live
        # investigation brief was cut off mid-sentence ("...to find") and still
        # returned as a success. The brief itself never exceeds MAX_BODY_CHARS;
        # the headroom exists so the reasoning cannot eat the answer.
        max_tokens=8000,
        stream=True,
        # Turning a spoken sentence plus a set of file outlines into a briefed
        # task is judgement work, not transcription. The documentation is
        # explicit that thinking disabled performs worse than a modest effort at
        # comparable cost, and "none" is what made this a rephraser.
        reasoning_effort="medium",
    )
    chunks: list[str] = []
    async for delta in brain.complete(request):
        if delta.content:
            chunks.append(delta.content)
    return "".join(chunks).strip()


async def _compose_once(
    *,
    brain,  # noqa: ANN001 - Brain, avoid an import cycle
    session,  # noqa: ANN001 - Session, avoid an import cycle
    said: str,
    base_instruction: str,
    terminal_name: str,
    agent_display: str,
    candidates: list[str],
    kind: str,
) -> str:
    """Read the candidate files, then make the one writing call.

    File reading runs in a worker thread: it is disk IO on a path a voice turn
    is waiting on, and the whole call already sits under ``COMPOSE_TIMEOUT_S``.
    """
    from . import prompt_blueprint as blueprint
    from .code_skeleton import skeletons as read_skeletons

    outlines = await asyncio.to_thread(read_skeletons, session.folder, candidates)
    house_rules = await asyncio.to_thread(_house_rules, session)

    return await _llm_compose(
        brain=brain,
        system_prompt=blueprint.system_prompt(kind),
        user_block=blueprint.user_block(
            utterance=said,
            instruction=base_instruction,
            terminal_name=terminal_name,
            agent_display=agent_display,
            profile_lines=session.profile.summary_lines(),
            candidates=candidates,
            skeletons=outlines,
            house_rules=house_rules,
        ),
    )


def _strip_wrapper(text: str) -> str:
    """Remove a fenced block or surrounding quotes a model may have added."""
    stripped = text.strip()
    fence = re.match(r"^```[a-zA-Z]*\s*\n(.*?)\n?```$", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    if len(stripped) > 1 and stripped[0] == stripped[-1] and stripped[0] in "\"'":
        stripped = stripped[1:-1].strip()
    return stripped


async def compose(
    utterance: str,
    *,
    session,  # noqa: ANN001 - Session, avoid an import cycle
    terminal_name: str,
    agent_display: str = "the coding agent",
    instruction: str | None = None,
    use_llm: bool = True,
    max_files: int = MAX_FILE_REFERENCES,
    brain=None,  # noqa: ANN001 - Brain, avoid an import cycle
) -> ComposedPrompt:
    """Build the prompt for ``terminal_name`` out of what the user said.

    ``brain`` pins the writing model explicitly. Leave it None — the default
    resolves a quality-tier model and degrades openly when none is reachable.
    Passing a fast model here trades prompt accuracy for a few seconds, which
    is the trade the maintainer decided against on 2026-07-25.

    Never raises: every failure path lands on the deterministic prompt, because
    "the agent got a rougher prompt" is a far better outcome than "your
    instruction vanished".
    """
    from . import prompt_blueprint as blueprint
    from .task_kind import classify

    said = (utterance or "").strip()
    base_instruction = _clean_speech(instruction or said) or (instruction or said).strip()
    if not said:
        return ComposedPrompt(text="", composed_by="raw", note="empty instruction")

    candidates = _file_candidates(session, base_instruction or said, max_files * 2)

    def _deterministic(composed_by: str, note: str = "") -> ComposedPrompt:
        chosen = candidates[:max_files]
        return ComposedPrompt(
            text=sanitize_prompt(
                blueprint.render_fallback(base_instruction, chosen), keep_newlines=True
            )[:MAX_PROMPT_CHARS],
            files=chosen,
            composed_by=composed_by,
            note=note,
        )

    if not use_llm:
        return _deterministic("raw")

    writer = brain if brain is not None else _resolve_writer()
    if writer is None:
        # Deliberately NOT falling through to whatever model is left: see the
        # module docstring. Plain and honest beats polished and quietly worse.
        return _deterministic("fallback", "no quality-tier provider reachable")

    try:
        composed = await asyncio.wait_for(
            _compose_once(
                brain=writer,
                session=session,
                said=said,
                base_instruction=base_instruction,
                terminal_name=terminal_name,
                agent_display=agent_display,
                candidates=candidates,
                kind=classify(base_instruction),
            ),
            timeout=COMPOSE_TIMEOUT_S,
        )
        composed = _strip_wrapper(composed)
    except TimeoutError:
        return _deterministic(
            "fallback", f"composer timed out after {COMPOSE_TIMEOUT_S:g}s"
        )
    except Exception as exc:  # noqa: BLE001 - any provider failure degrades
        logger.info("Agentic IDE prompt composer fell back: {}", exc)
        return _deterministic("fallback", f"composer unavailable ({type(exc).__name__})")

    if not composed:
        return _deterministic("fallback", "composer returned nothing usable")

    if not blueprint.looks_like_brief(composed):
        # A subscription CLI writes its own errors to stdout, so "the process
        # answered" is not the same as "a brief came back". Live 2026-07-26: one
        # returned a one-line flag-validation error that shipped as the composed
        # prompt. Anything without a single heading is debris, not a task.
        logger.info(
            "Agentic IDE prompt composer returned something that is not a brief "
            "({} chars) — falling back",
            len(composed),
        )
        return _deterministic("fallback", "composer output was not a brief")

    if blueprint.looks_truncated(composed):
        # Half a brief reads as a whole one, which is exactly what makes it
        # dangerous: the agent starts on an instruction whose second half was
        # never written. The plain deterministic prompt is worse but complete.
        logger.info(
            "Agentic IDE prompt composer produced a truncated brief ({} chars) "
            "— falling back",
            len(composed),
        )
        return _deterministic("fallback", "composer output was cut off")

    # Keep only the references that survive an existence check — the model may
    # echo a candidate that was renamed, or invent one outright. A dead @path
    # costs the agent a turn; the bare path costs it nothing.
    referenced = _existing(session.folder, _extract_referenced(composed))
    for bad in (r for r in _extract_referenced(composed) if r not in referenced):
        composed = composed.replace(f"@{bad}", bad)

    if blueprint.ends_on_reference(composed):
        # A trailing @path or /command holds the completion popup open, and the
        # Enter that follows picks a suggestion instead of submitting.
        composed = f"{composed}\n\nStart there; search further if needed."

    final = sanitize_prompt(composed, keep_newlines=True)[:MAX_PROMPT_CHARS]
    if not final:
        return _deterministic("fallback", "composer returned nothing usable")
    return ComposedPrompt(text=final, files=referenced, composed_by="llm")


__all__ = [
    "COMPOSE_TIMEOUT_S",
    "MAX_FILE_REFERENCES",
    "ComposedPrompt",
    "compose",
]
