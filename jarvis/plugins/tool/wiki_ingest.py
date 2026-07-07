"""``wiki-ingest`` tool — explicit brain-driven wiki write path.

B5 follow-up (post-merge).  Router-tier tool with write semantics.

Why this tool exists alongside the voice-bridge
-----------------------------------------------
:class:`jarvis.memory.wiki.voice_bridge.VoiceFactBridge` already pushes
voice-spoken facts to the wiki via two heuristics: the *ack path* (brain
reply contains "notiert"/"vermerkt"/...) and the *aggressive path*
(every user turn >= ``min_user_chars`` is curator-filtered).

Heuristics drift.  When the brain consciously decides "this is worth
storing" (e.g. user typed something into chat, or the brain is
summarising a long subagent run), it should be able to call a tool
explicitly instead of relying on the bridge picking it up.  That is what
this tool provides.

Architecture
------------
The tool delegates to the live :class:`~jarvis.memory.wiki.curator.WikiCurator`
instance owned by ``bootstrap_wiki_integration``.  The curator handles
salience filtering (an empty source returns zero updates, no harm done),
atomic writing with backup/rollback, and log-entry rendering.  This tool
adds nothing beyond the dispatch.

Risk tier and confirmation
--------------------------
Marked ``safe``.  The write surface is protected by ``AtomicWriter``
(AP-3 pre-validate, AP-4 backup) and the curator's salience filter; the
tool itself has no destructive scope.  Bypass-confirmation matches
``wiki-recall`` so the brain can call both freely.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from jarvis.core.protocols import ToolResult

log = logging.getLogger(__name__)


# Minimum length to even attempt an ingest.  Below this the curator's
# salience filter would almost certainly drop the content and we save
# the LLM call.  Mirrors the ``_MIN_ACK_USER_CHARS`` constant in
# voice_bridge.py.
_MIN_INGEST_CHARS: int = 12

# Hard cap on accepted content length.  Wiki pages are KB-sized; anyone
# trying to push a megabyte through here is either confused or
# malicious.  ``WikiCuratorLLM`` truncates internally too, but failing
# fast is cleaner.
_MAX_INGEST_CHARS: int = 32_000


class WikiIngestTool:
    """Router-tier deterministic-ingest path into the long-term wiki vault."""

    name: str = "wiki-ingest"
    description: str = (
        "Store a fact, observation, or summary in the user's long-term Obsidian "
        "wiki. Use this when the user explicitly asks you to remember something "
        "('merk dir', 'speichere', 'remember that …') or when you have finished "
        "a substantial piece of work whose outcome should outlive this session. "
        "The curator decides which pages to touch — give it the content as one "
        "self-contained block."
    )
    # H10 (2026-05-17 audit): `safe` is wrong for an LLM-and-disk tool.
    # wiki-ingest spawns the WikiCuratorLLM (Gemini/Grok call) and writes
    # markdown pages under the vault; that is the precise definition of
    # `monitor` per the project's risk-tier taxonomy -- run without a
    # user prompt (anti-confirmation-fatigue) but log every invocation
    # for audit. `ask` would nag the user on every "merk dir das"; `safe`
    # falsely implied no side effects.
    risk_tier: str = "monitor"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": (
                    "The fact or summary to ingest.  Plain prose; do not "
                    "pre-format as a wiki page — the curator does that."
                ),
            },
            "source": {
                "type": "string",
                "description": (
                    "Optional short label describing where the content came "
                    "from, e.g. 'chat:2026-05-17' or 'voice:user-direct'. "
                    "Used in the log entry."
                ),
            },
        },
        "required": ["text"],
    }
    input_examples: list[dict[str, Any]] = [
        {
            "text": (
                "Joy's birthday is August 14th. She is Sam's younger "
                "sister."
            ),
            "source": "voice:user-direct",
        },
        {
            "text": "We finished Phase B5 today — the brain now reads from the wiki.",
            "source": "chat:milestone",
        },
    ]

    def __init__(
        self,
        *,
        curator_resolver: Callable[[], Any],
    ) -> None:
        # Lazy resolver so the curator can be set up after the brain is
        # built (mirrors the spawn-worker lazy-resolver pattern in
        # ``factory.py:_load_tools_for_tier``).
        self._resolve_curator = curator_resolver

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        text = str(args.get("text", "")).strip()
        source = str(args.get("source") or "tool:wiki-ingest").strip() or "tool:wiki-ingest"

        if not text:
            return ToolResult(success=False, output="", error="missing 'text' argument")

        if len(text) < _MIN_INGEST_CHARS:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"text too short ({len(text)} chars; min {_MIN_INGEST_CHARS}). "
                    "Pass a full self-contained statement, not a single word."
                ),
            )

        if len(text) > _MAX_INGEST_CHARS:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"text too long ({len(text)} chars; max {_MAX_INGEST_CHARS}). "
                    "Split into multiple ingest calls."
                ),
            )

        curator = self._resolve_curator()
        if curator is None:
            log.warning("wiki-ingest: no live WikiCurator registered (bootstrap not run?)")
            return ToolResult(
                success=False,
                output="",
                error="wiki integration not bootstrapped",
            )

        # Privacy: text is logged at DEBUG only (full content), source at INFO.
        log.debug("wiki-ingest: source=%s len=%d body=%r", source, len(text), text)
        log.info("wiki-ingest: ingesting %d chars (source=%s)", len(text), source)

        try:
            result = await curator.ingest(text, source)
        except Exception as exc:  # noqa: BLE001
            log.warning("wiki-ingest: curator.ingest raised %s", exc)
            return ToolResult(
                success=False,
                output="",
                error=f"curator ingest failed: {exc}",
            )

        applied = list(getattr(result, "applied", []) or [])
        skipped = list(getattr(result, "skipped_due_to_recent_edit", []) or [])
        failed = list(getattr(result, "failed_validation", []) or [])

        if not applied and not skipped and not failed:
            # Curator judged the content not salient. The old success=True here made
            # the model tell users "stored" when NOTHING was written (fresh-machine
            # forensics Bug 12/18) -- a no-op is a failure from the caller's intent.
            log.info("wiki-ingest: curator returned no updates (salience filter)")
            return ToolResult(
                success=False,
                output="",
                error=(
                    "nothing was stored: the curator judged the content not salient "
                    "enough for the wiki. Tell the user the wiki was NOT updated; if "
                    "they explicitly asked to store this, apologize and suggest they "
                    "add it via the Wiki view instead."
                ),
            )

        summary_lines = [
            f"Wiki ingest done (source={source}):",
            f"- applied: {len(applied)}",
        ]
        if skipped:
            summary_lines.append(f"- skipped (recent user edit): {len(skipped)}")
        if failed:
            summary_lines.append(f"- failed validation (rolled back): {len(failed)}")
        if applied:
            summary_lines.append("Pages touched:")
            for page_path in applied[:10]:
                # ``applied`` is a list of Path objects per WriteResult contract.
                try:
                    summary_lines.append(f"  - {page_path.name}")
                except AttributeError:
                    summary_lines.append(f"  - {page_path}")

        log.info(
            "wiki-ingest: applied=%d skipped=%d failed=%d",
            len(applied),
            len(skipped),
            len(failed),
        )

        return ToolResult(success=True, output="\n".join(summary_lines))
