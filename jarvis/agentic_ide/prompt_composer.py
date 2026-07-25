"""Turn a spoken instruction into a prompt a coding agent can actually work on.

This is the part that makes the Agentic IDE worth having. Speaking at an agent
and speaking at a *transcript relay* are very different products: dictated
words arrive as one unpunctuated run of filler, pronouns and self-corrections
("kannst du mal schnell, also ähm, das Wake-Ding angucken ob da was kaputt
ist"), and pasting that into Claude Code produces a confused agent that starts
by asking what you meant. What the user asked for is that *Jarvis* does the
prompt engineering: it knows the repo, so it turns that sentence into a briefed
task with the relevant files already attached.

What the composed prompt contains, and why each part earns its place:

* **The task, as an imperative.** Fillers, the addressing clause ("tell Kai
  to …") and speech artefacts are gone; what remains is what to do.
* **File references in the agent's own syntax** (``@path``). Both supported
  CLIs read ``@`` as "pull this file into context", which removes the agent's
  entire opening round of blind searching. Paths come from the workspace file
  index — never invented, and every one is verified to exist before it ships.
* **A short workspace line** (stack, branch) when it is not obvious, so the
  agent does not re-derive it.

Two-layer construction, because the product must work for a downloader with no
API key at all (§3):

1. **Composed** — one bounded call to the frontier chain
   (``resolve_frontier_brain``: key-aware, crosses provider families, AP-22).
   It gets the utterance, the project profile and the candidate files, and
   returns the prompt.
2. **Deterministic fallback** — regex cleanup plus the same file references,
   used when no provider is reachable, the call fails, times out, or comes back
   empty. It is *always* better than the raw transcript, so the feature never
   depends on a model being available.

The composer is honest about which layer produced the result (``composed_by``),
because the readback the user hears should not claim more than happened.
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
# waiting to hear "sent to Kai" — past a few seconds it feels broken. On timeout
# the deterministic prompt ships, so this bound costs quality, never delivery.
#
# Measured 2026-07-25 on the live chain: a fast router-tier model answers in
# 1-3 s, while the deep frontier tier (a thinking model) took 7-8 s for the same
# rewrite. Callers that hold a fast brain pass it in; 12 s leaves room for the
# slow path to still succeed rather than silently demoting every composition to
# the regex fallback, which an 8 s bound did.
COMPOSE_TIMEOUT_S = 12.0

# How many files may be attached. Enough to point the agent at a feature's
# surface; few enough that the agent's context is not flooded with guesses.
MAX_FILE_REFERENCES = 5

_SYSTEM_PROMPT = """\
You turn a spoken instruction into a precise prompt for a coding agent (Claude \
Code / Codex) that is already running inside the user's repository.

Rules:
- Output ONLY the prompt text. No preamble, no explanation, no quotes, no \
markdown fences.
- Write it as a direct instruction to the coding agent, in the imperative.
- Preserve every constraint, file, symbol and intent the user expressed. Do not \
invent requirements, scope, or acceptance criteria they did not state.
- Remove speech artefacts: filler words, false starts, self-corrections, and \
the clause that addressed the agent by name ("tell Kai to ...").
- If the user was vague, keep it vague — state the goal and let the agent \
investigate. Never fabricate specifics to make the prompt look complete.
- Reference relevant files with @path syntax on their own line at the end, \
using ONLY paths from the candidate list you are given. Omit the line entirely \
if no candidate is clearly relevant.
- Write the prompt in the same language the user spoke.
- Keep it under 1200 characters.
"""

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


def _render_fallback(instruction: str, files: list[str]) -> str:
    body = _clean_speech(instruction)
    if not body:
        body = " ".join((instruction or "").split())
    if files:
        body = f"{body}\n\n{' '.join('@' + f for f in files)}"
    return body


def _extract_referenced(text: str) -> list[str]:
    """``@path`` tokens in a composed prompt, in order of appearance."""
    seen: list[str] = []
    for match in re.finditer(r"@([\w./\\-]+)", text or ""):
        rel = match.group(1).replace("\\", "/")
        if rel not in seen:
            seen.append(rel)
    return seen


async def _llm_compose(
    *,
    utterance: str,
    instruction: str,
    session,  # noqa: ANN001 - Session, avoid an import cycle
    terminal_name: str,
    agent_display: str,
    candidates: list[str],
    brain=None,  # noqa: ANN001 - Brain, avoid an import cycle
) -> str:
    """One bounded call that writes the prompt.

    ``brain`` lets a caller that already holds a warm, FAST model pass it in —
    the router-tier brain the voice turn is using anyway. That matters: measured
    on the live chain, a rewrite took 1-3 s there against 7-8 s on the deep
    frontier tier, which is a thinking model doing far more work than rephrasing
    one sentence needs.

    Without one (the REST/CLI path, which holds no brain), it falls back to
    ``resolve_frontier_brain`` — the shared key-aware chain that follows the
    user's own configuration and crosses provider families when the primary is
    dead (AP-21 / AP-22), rather than a pinned provider that would brick this
    for every downloader with a different key.
    """
    from jarvis.core.protocols import BrainMessage, BrainRequest

    if brain is None:
        from jarvis.brain.resolver import resolve_frontier_brain
        from jarvis.core.config import load_config

        brain = resolve_frontier_brain(load_config())

    profile_lines = "\n".join(session.profile.summary_lines())
    candidate_block = (
        "\n".join(f"- {c}" for c in candidates)
        if candidates
        else "(no candidate files matched — omit the @ line)"
    )
    user_block = (
        f"The user is talking to the coding agent {agent_display} running in a "
        f"terminal they call {terminal_name}.\n\n"
        f"WORKSPACE\n{profile_lines}\n\n"
        f"CANDIDATE FILES (repo-relative; use only these in @ references)\n"
        f"{candidate_block}\n\n"
        f"WHAT THE USER SAID (verbatim speech transcript)\n{utterance}\n\n"
        f"THE INSTRUCTION PART, WITH THE ADDRESSING REMOVED\n{instruction}\n\n"
        f"Write the prompt now."
    )

    request = BrainRequest(
        messages=(BrainMessage(role="user", content=user_block),),
        system=_SYSTEM_PROMPT,
        # Deterministic rewriting, not creative writing: the prompt must carry
        # the user's intent, so temperature stays low.
        temperature=0.2,
        max_tokens=800,
        stream=True,
        # A prompt rewrite needs no internal reasoning budget, and on
        # thinking-heavy models it would eat max_tokens and truncate the answer.
        reasoning_effort="none",
    )
    chunks: list[str] = []
    async for delta in brain.complete(request):
        if delta.content:
            chunks.append(delta.content)
    return "".join(chunks).strip()


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

    Pass ``brain`` when you already hold a fast one (see ``_llm_compose``);
    omitting it resolves the shared key-aware chain.

    Never raises: every failure path lands on the deterministic prompt, because
    "the agent got a slightly rougher prompt" is a far better outcome than "your
    instruction vanished".
    """
    said = (utterance or "").strip()
    base_instruction = (instruction or said).strip()
    if not said:
        return ComposedPrompt(text="", composed_by="raw", note="empty instruction")

    candidates = _file_candidates(session, base_instruction or said, max_files * 2)

    if not use_llm:
        text = _render_fallback(base_instruction, candidates[:max_files])
        return ComposedPrompt(
            text=sanitize_prompt(text)[:MAX_PROMPT_CHARS],
            files=candidates[:max_files],
            composed_by="raw",
        )

    note = ""
    try:
        composed = await asyncio.wait_for(
            _llm_compose(
                utterance=said,
                instruction=base_instruction,
                session=session,
                terminal_name=terminal_name,
                agent_display=agent_display,
                candidates=candidates,
                brain=brain,
            ),
            timeout=COMPOSE_TIMEOUT_S,
        )
        composed = _strip_wrapper(composed)
    except TimeoutError:
        composed, note = "", f"composer timed out after {COMPOSE_TIMEOUT_S:g}s"
    except Exception as exc:  # noqa: BLE001 - any provider failure degrades
        composed, note = "", f"composer unavailable ({type(exc).__name__})"
        logger.info("Agentic IDE prompt composer fell back: {}", exc)

    if composed:
        # Keep only the references that survive an existence check — the model
        # may echo a candidate that was renamed, or invent one outright.
        referenced = _existing(session.folder, _extract_referenced(composed))
        dropped = [r for r in _extract_referenced(composed) if r not in referenced]
        for bad in dropped:
            composed = composed.replace(f"@{bad}", bad)
        final = sanitize_prompt(composed)[:MAX_PROMPT_CHARS]
        if final:
            return ComposedPrompt(
                text=final, files=referenced, composed_by="llm", note=note
            )
        note = note or "composer returned nothing usable"

    chosen = candidates[:max_files]
    return ComposedPrompt(
        text=sanitize_prompt(_render_fallback(base_instruction, chosen))[:MAX_PROMPT_CHARS],
        files=chosen,
        composed_by="fallback",
        note=note or "no provider reachable",
    )


__all__ = [
    "COMPOSE_TIMEOUT_S",
    "MAX_FILE_REFERENCES",
    "ComposedPrompt",
    "compose",
]
