"""``SessionRollupWorker`` — Phase B7, mid-term memory tier.

Watches the awareness ``IdleEntered`` event stream and turns each
finished work session into one Markdown digest under
``wiki/obsidian-vault/sessions/<YYYY-MM-DD>-<short-id>.md``.

A *session* is a contiguous block of activity bounded by a long-enough
idle stretch (default 2h). When the worker sees an ``IdleEntered`` event
whose ``idle_since_ns`` is more than ``session_idle_threshold_minutes``
in the past, it:

1. Reads every awareness episode since the current ``session_start_ns``.
2. Calls the configured Brain provider (same fallback chain as the
   wiki curator: ``cfg.provider or brain.primary``) for a one-paragraph
   rollup.
3. Renders a ``type: session`` Markdown page conforming to ``schema.md``.
4. Writes it via the shared ``AtomicWriter`` so we get the same backup
   + validate + rollback safety net the rest of the wiki has.
5. Archives any session beyond ``max_active_sessions`` into
   ``_archive/sessions/`` so Obsidian's sidebar stays readable.
6. Appends one ``log.md`` entry.
7. Sets ``session_start_ns`` to the current time so the next session
   starts cleanly.

The worker also exposes ``flush_session()`` as a public method so the
voice path (Phase B5) and the day-rollover hook can trigger a rollup
without going through ``IdleEntered``.

Failure modes (all return :class:`SessionRollupResult` with a descriptive
``status`` — never raise):

* fewer than ``min_episodes_for_rollup`` episodes since session start →
  ``skipped_too_few_episodes``
* brain unavailable / not in registry → ``llm_unavailable``
* brain timeout (outer ``asyncio.wait_for`` above ``cfg.timeout_s``) →
  ``llm_timeout``
* brain raises any exception → ``llm_failure``
* atomic writer rolled back the rendered page (schema-invalid) →
  ``rollback``
* atomic writer skipped the page because of the 30s concurrent-edit
  lock → ``skipped_recent_edit``

These are all "safe" outcomes — no crash, no half-written file, no log
entry that lies about what happened.
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus
    from jarvis.core.config import JarvisConfig, SessionRollupConfig
    from jarvis.memory.recall import RecallStore

    from .atomic_writer import AtomicWriter
    from .log_writer import LogWriter
    from .protocols import PageRepository

from jarvis.brain.provider_registry import BrainProviderRegistry
from jarvis.brain.streaming import aggregate, is_length_truncated
from jarvis.core.events import IdleEntered
from jarvis.core.protocols import BrainMessage, BrainRequest

from .curator_llm import _resolve_provider_and_model
from .prompt import select_top_slugs
from .protocols import PageUpdate
from .session_links import (
    SlugIndex,
    build_related_footer,
    rewrite_body_links,
    strip_dangling_wikilinks,
)
from .telemetry import telemetry

log = logging.getLogger(__name__)

# Page-type directories holding durable pages a session may link into.
# Sessions deliberately do NOT link other sessions (avoids a noisy chain);
# they link the user, projects, and the concepts/entities they reference.
_DURABLE_DIRS: tuple[str, ...] = ("entities", "concepts", "projects")

# Matches the frontmatter ``aliases:`` line so a Title-Case mention in the
# LLM paragraph can resolve to a page via its declared alias.
_ALIASES_RE = re.compile(r"^aliases:\s*(.+)$", re.MULTILINE)


# ----------------------------------------------------------------------------
# Result + status
# ----------------------------------------------------------------------------

RollupStatus = str
"""``"ok" | "skipped_too_few_episodes" | "skipped_recent_edit" |
"llm_unavailable" | "llm_timeout" | "llm_failure" | "rollback" |
"disabled" | "disabled_wiki_write"``"""


@dataclass(frozen=True, slots=True)
class SessionRollupResult:
    """Outcome of one ``flush_session()`` call.

    The status field is the primary signal; ``page_path`` is filled only
    when a page actually landed on disk, ``episode_count`` and
    ``summary_chars`` are informational for logs and tests.
    """

    status: RollupStatus
    episode_count: int = 0
    page_path: Path | None = None
    summary_chars: int = 0
    archived: tuple[Path, ...] = ()


# ----------------------------------------------------------------------------
# Worker
# ----------------------------------------------------------------------------


class SessionRollupWorker:
    """Lifecycle-managed session-rollup worker.

    Construct one instance per Jarvis process. Call ``await
    worker.start()`` on app bootstrap, ``await worker.stop()`` on
    shutdown. While running it subscribes to ``IdleEntered`` and may
    fire one rollup per long-idle event.
    """

    def __init__(
        self,
        *,
        config: "JarvisConfig",
        recall_store: "RecallStore",
        vault_root: Path,
        atomic_writer: "AtomicWriter",
        page_repo: "PageRepository",
        log_writer: "LogWriter",
        bus: "EventBus",
        clock: Callable[[], int] | None = None,
        registry: BrainProviderRegistry | None = None,
    ) -> None:
        self._config = config
        self._cfg: SessionRollupConfig = config.memory.wiki.session_rollup
        self._recall = recall_store
        self._vault_root = Path(vault_root).resolve()
        self._writer = atomic_writer
        self._repo = page_repo
        self._log = log_writer
        self._bus = bus
        self._clock = clock or time.time_ns
        self._registry = registry or BrainProviderRegistry()

        # Stateful: the worker remembers when the current session began.
        # We initialise to "now" so the first rollup only covers episodes
        # produced after this worker booted — older episodes belong to a
        # previous process's session and have either been rolled up
        # already or were lost to a crash.
        self._session_start_ns: int = self._clock()
        self._brain: Any | None = None
        self._brain_lock = asyncio.Lock()
        self._subscribed: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Subscribe to ``IdleEntered``. Idempotent."""
        if not self._cfg.enabled:
            log.info("SessionRollupWorker disabled via config; not subscribing")
            return
        if self._subscribed:
            return
        self._bus.subscribe(IdleEntered, self._on_idle_entered)
        self._subscribed = True
        log.info(
            "SessionRollupWorker started (threshold=%dmin, min_episodes=%d, "
            "max_active=%d, vault=%s)",
            self._cfg.session_idle_threshold_minutes,
            self._cfg.min_episodes_for_rollup,
            self._cfg.max_active_sessions,
            self._vault_root,
        )

    async def stop(self) -> None:
        """Unsubscribe from ``IdleEntered``. Idempotent."""
        if not self._subscribed:
            return
        try:
            self._bus.unsubscribe(IdleEntered, self._on_idle_entered)
        except Exception:    # noqa: BLE001
            # Some EventBus implementations only support symmetric
            # subscribe/unsubscribe; if ours does not, treat it as
            # already-detached and keep going.
            log.debug("EventBus.unsubscribe failed; treating as detached")
        self._subscribed = False
        log.info("SessionRollupWorker stopped")

    # ------------------------------------------------------------------
    # Public API (also used by tests + Phase B5 voice integration)
    # ------------------------------------------------------------------

    @property
    def session_start_ns(self) -> int:
        """Timestamp of the current session's start.

        Reads as ns-since-epoch like ``time.time_ns()``. Used by tests
        and the voice trigger to scope a manual flush.
        """
        return self._session_start_ns

    async def flush_session(self) -> SessionRollupResult:
        """Roll up everything since ``session_start_ns`` into one page.

        Public entry point — called by ``_on_idle_entered`` for the
        idle-threshold trigger and by external callers (voice "tschüss",
        day-rollover timer) for explicit flushes.
        """
        if not self._cfg.enabled:
            return SessionRollupResult(status="disabled")

        # D2 (2026-06): the awareness-episode -> durable session-page feed is
        # retired. Awareness L1/L2 keeps recording episodes; we simply stop
        # turning them into wiki pages here. Conversation (VoiceFactBridge)
        # is now the sole wiki feed. Short-circuit before the brain call and
        # the AtomicWriter so this path produces neither a page nor an LLM
        # round-trip.
        if not getattr(self._cfg, "wiki_write_enabled", False):
            telemetry.inc("session_rollups_wiki_write_disabled")
            log.debug(
                "SessionRollupWorker: wiki_write_enabled is off — "
                "not writing a session page from awareness episodes"
            )
            # Advance the session marker so a later re-enable starts a clean
            # window rather than replaying the whole backlog.
            self._session_start_ns = self._clock()
            return SessionRollupResult(status="disabled_wiki_write")

        episodes = await self._recall.recent_episodes(
            limit=1000,
            since_ns=self._session_start_ns,
        )
        # ``recent_episodes`` returns DESC by started_at_ns — flip to
        # chronological so the LLM sees events in the order they happened.
        episodes = list(reversed(episodes))

        if len(episodes) < self._cfg.min_episodes_for_rollup:
            log.debug(
                "SessionRollupWorker: %d episodes < min %d, skipping",
                len(episodes),
                self._cfg.min_episodes_for_rollup,
            )
            # Even when skipping, advance the session marker so a
            # follow-up burst of activity starts a fresh session.
            self._session_start_ns = self._clock()
            return SessionRollupResult(
                status="skipped_too_few_episodes",
                episode_count=len(episodes),
            )

        # ----- Slug index for graph-aware linking ---------------------
        # Built once from the current durable vault pages. Used to (a) tell
        # the LLM which pages it may link, and (b) resolve/normalise the
        # links it returns. Off the voice critical path (AP-9), so the
        # synchronous vault scan is fine here.
        durable_pages = self._scan_durable_pages()
        slug_index = SlugIndex.from_pages(durable_pages)

        # ----- LLM call ------------------------------------------------
        summary = await self._call_brain(episodes, durable_pages)
        if summary is None:
            # The specific failure (timeout, unavailable, exception)
            # is logged inside _call_brain; we map to the closest status
            # the caller can act on.
            telemetry.inc("session_rollups_failed")
            return SessionRollupResult(
                status="llm_failure",
                episode_count=len(episodes),
            )

        # ----- Graph-connectivity post-processing ---------------------
        # Strip truncated fragments, demote ghost links to plain text,
        # canonicalise resolvable ones, and build the durable-hub footer so
        # the session joins the network instead of scattering. Deterministic,
        # regex only — no extra LLM round-trip.
        summary, hub_links, resolved = self._postprocess_summary(
            summary, episodes, slug_index, durable_pages
        )
        related_footer = build_related_footer(
            hub_links=hub_links, resolved_targets=resolved
        )

        # ----- Render + write -----------------------------------------
        session_id = self._gen_session_id()
        started_at_ns = int(episodes[0]["started_at_ns"])
        ended_at_ns = int(episodes[-1]["ended_at_ns"])
        date_str = _to_local_date(started_at_ns)
        page_path = self._vault_root / "sessions" / f"{date_str}-{session_id}.md"

        body = self._render_session_page(
            episodes=episodes,
            summary=summary,
            session_id=session_id,
            started_at_ns=started_at_ns,
            ended_at_ns=ended_at_ns,
            related_footer=related_footer,
        )

        update = PageUpdate(
            target_path=page_path,
            operation="create",
            new_body=body,
            reason="session rollup",
        )
        write_result = await self._writer.apply([update], repo=self._repo)

        if page_path in write_result.skipped_due_to_recent_edit:
            log.warning(
                "SessionRollupWorker: skipped %s — file was edited in the last 30s",
                page_path.name,
            )
            telemetry.inc("session_rollups_failed")
            return SessionRollupResult(
                status="skipped_recent_edit",
                episode_count=len(episodes),
            )

        if page_path in write_result.failed_validation:
            log.warning(
                "SessionRollupWorker: rendered session page failed schema "
                "validation, rolled back: %s",
                page_path.name,
            )
            telemetry.inc("session_rollups_failed")
            return SessionRollupResult(
                status="rollback",
                episode_count=len(episodes),
            )

        # ----- Rolling window: archive oldest beyond the cap ----------
        # Deliberately synchronous on the event loop: the loop serialises
        # the rename batch against concurrent AtomicWriter snapshots (a
        # to_thread version races the voice-ingest backup walk, which then
        # fails on a file vanishing mid-tar). Bounded by
        # max_active_sessions (default 5) renames — microseconds.
        archived = self._archive_old_sessions()
        if archived:
            # The archiver renames files directly (bypassing AtomicWriter), so
            # purge the FTS rows keyed by their ORIGINAL live path — otherwise
            # search returns ghost hits at the now-empty ``sessions/<name>.md``.
            source_paths = [
                self._vault_root / "sessions" / dst.name for dst in archived
            ]
            await asyncio.to_thread(self._writer.forget_paths, source_paths)

        # ----- Log entry ----------------------------------------------
        # Touch the session page plus every durable hub it linked, so
        # log-driven backlinks also pull the session into the graph.
        touched_targets = list(dict.fromkeys(hub_links + resolved))
        pages_touched = [f"[[sessions/{date_str}-{session_id}]]"] + [
            f"[[{target}]]" for target in touched_targets
        ]
        await self._log.append_log_entry(
            verb="merge",
            subject=f"session rollup {date_str}-{session_id}",
            pages_touched=pages_touched,
            source=f"awareness L2 episodes ({len(episodes)})",
            summary=(
                f"Rolled up {len(episodes)} episodes into one session page "
                f"({len(summary)} chars). "
                + (
                    f"Archived {len(archived)} older session(s) past the "
                    f"{self._cfg.max_active_sessions}-cap."
                    if archived
                    else "No archiving needed."
                )
            ),
        )

        # ----- Advance the session marker -----------------------------
        self._session_start_ns = self._clock()

        telemetry.inc("session_rollups_succeeded")
        return SessionRollupResult(
            status="ok",
            episode_count=len(episodes),
            page_path=page_path,
            summary_chars=len(summary),
            archived=tuple(archived),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _on_idle_entered(self, event: IdleEntered) -> None:
        """Bus handler — checks the threshold and delegates to ``flush_session``."""
        now_ns = self._clock()
        idle_since_ns = event.idle_since_ns or now_ns
        idle_ns = now_ns - idle_since_ns
        threshold_ns = self._cfg.session_idle_threshold_minutes * 60 * 1_000_000_000

        if idle_ns < threshold_ns:
            log.debug(
                "SessionRollupWorker: idle %.1fmin < threshold %dmin, not flushing",
                idle_ns / 6e10,
                self._cfg.session_idle_threshold_minutes,
            )
            return

        log.info(
            "SessionRollupWorker: idle threshold reached (%.1fmin) — flushing session",
            idle_ns / 6e10,
        )
        await self.flush_session()

    async def _call_brain(
        self,
        episodes: list[dict[str, Any]],
        durable_pages: list[tuple[str, str, list[str]]],
    ) -> str | None:
        """Render the prompt, call the brain, return the digest text.

        Returns ``None`` on any failure. The caller maps that to the
        appropriate :class:`SessionRollupResult` status.
        """
        prompt = self._build_prompt(episodes, durable_pages)
        provider_name, model = _resolve_provider_and_model(
            self._cfg, self._config
        )

        try:
            async with self._brain_lock:
                if self._brain is None:
                    self._brain = await asyncio.to_thread(
                        self._registry.instantiate,
                        provider_name,
                        model=model,
                    )
        except Exception as exc:    # noqa: BLE001
            log.warning(
                "SessionRollupWorker: brain provider %r unavailable: %s",
                provider_name, exc,
            )
            return None

        request = BrainRequest(
            messages=(BrainMessage(role="user", content=prompt),),
            max_tokens=self._cfg.max_output_tokens,
            temperature=0.4,                    # factual editor, not creative
            stream=True,
        )

        try:
            agg = await asyncio.wait_for(
                aggregate(self._brain.complete(request)),
                timeout=self._cfg.timeout_s,
            )
        except (asyncio.TimeoutError, TimeoutError):
            log.warning(
                "SessionRollupWorker: brain timed out after %.1fs",
                self._cfg.timeout_s,
            )
            return None
        except Exception as exc:    # noqa: BLE001
            log.warning("SessionRollupWorker: brain call raised: %s", exc)
            return None

        if is_length_truncated(agg.finish_reason, agg.text):
            log.warning(
                "SessionRollupWorker: digest hit the output-token cap "
                "(finish_reason=%r, %d chars) — discarding truncated paragraph "
                "rather than writing a half-finished session page",
                agg.finish_reason, len(agg.text or ""),
            )
            telemetry.inc("wiki_writes_blocked_truncated")
            return None

        text = (agg.text or "").strip()
        if not text:
            log.warning("SessionRollupWorker: brain returned empty text")
            return None
        return text

    def _build_prompt(
        self,
        episodes: list[dict[str, Any]],
        durable_pages: list[tuple[str, str, list[str]]],
    ) -> str:
        """Render the LLM input.

        The prompt is intentionally compact and instructional. The schema
        asks the LLM to produce one paragraph of prose under 400 words. To
        keep the Obsidian graph connected rather than scattered, the LLM is
        given the list of pages that actually exist and told to link ONLY
        those — every other app or tool is mentioned as plain text. A
        deterministic post-pass (``rewrite_body_links``) enforces this even
        when the model ignores the instruction, so the prompt is a hint, not
        a guarantee.
        """
        lines: list[str] = []
        lines.append(
            "You are summarising a single work session for a personal "
            "assistant's long-term memory."
        )
        lines.append(
            "Produce ONE flowing paragraph (max 400 words) capturing the "
            "main themes, decisions, and any open threads. "
            "Output ONLY the paragraph — no heading, no JSON, no preamble."
        )
        lines.append("")
        if durable_pages:
            allowed = ", ".join(
                f"[[{directory}/{slug}]]"
                for directory, slug, _aliases in durable_pages[:40]
            )
            lines.append(
                "You MAY link the following existing wiki pages, using the "
                "EXACT kebab-case form shown: " + allowed
            )
            lines.append(
                "Link ONLY pages from that list. Refer to applications, OS "
                "utilities, installers and one-off tools (e.g. PowerShell, "
                "an updater, a file picker) as plain text — never as "
                "[[wikilinks]]. Do not invent links to pages that are not "
                "listed."
            )
        else:
            lines.append(
                "Refer to applications, OS utilities, installers and one-off "
                "tools as plain text — never as [[wikilinks]]."
            )
        lines.append("")
        lines.append("Episodes (chronological):")
        for ep in episodes:
            ts = _to_local_hhmm(int(ep.get("started_at_ns", 0)))
            app = ep.get("primary_app", "?")
            summary = (ep.get("summary") or "").strip().replace("\n", " ")
            if len(summary) > 400:
                summary = summary[:400] + "…"
            lines.append(f"- [{ts}, {app}] {summary}")
        lines.append("")
        lines.append("Write the rollup paragraph now.")
        return "\n".join(lines)

    def _render_session_page(
        self,
        *,
        episodes: list[dict[str, Any]],
        summary: str,
        session_id: str,
        started_at_ns: int,
        ended_at_ns: int,
        related_footer: str = "",
    ) -> str:
        """Render a ``type: session`` Markdown page per the schema.

        ``related_footer`` is the deterministic ``## Related`` backbone block
        (empty when nothing resolves) appended after the prose so the session
        is wired into the graph.
        """
        date_str = _to_local_date(started_at_ns)
        started_hhmm = _to_local_hhmm(started_at_ns)
        ended_hhmm = _to_local_hhmm(ended_at_ns)
        episode_ids = [str(ep.get("id", "")) for ep in episodes if ep.get("id") is not None]

        frontmatter_lines = [
            "---",
            "type: session",
            f"date: {date_str}",
            f"started_at: {started_hhmm}",
            f"ended_at: {ended_hhmm}",
            f"episode_ids: [{', '.join(episode_ids)}]",
            f"session_id: {session_id}",
            "---",
        ]
        body_lines = [
            "",
            f"# Session {date_str} ({started_hhmm}–{ended_hhmm})",
            "",
            summary.strip(),
            "",
        ]
        if related_footer:
            body_lines.append(related_footer)
            body_lines.append("")
        return "\n".join(frontmatter_lines + body_lines)

    # ------------------------------------------------------------------
    # Graph-connectivity helpers
    # ------------------------------------------------------------------

    def _scan_durable_pages(self) -> list[tuple[str, str, list[str]]]:
        """Scan the durable page directories for ``(dir, slug, aliases)``.

        Reads ``entities/`` ``concepts/`` ``projects/`` synchronously (small,
        bounded, off the voice critical path). The slug is the filename stem;
        aliases come from the ``aliases:`` frontmatter line so a Title-Case
        mention can resolve through them. Unreadable files are skipped.
        """
        pages: list[tuple[str, str, list[str]]] = []
        for directory in _DURABLE_DIRS:
            page_dir = self._vault_root / directory
            if not page_dir.is_dir():
                continue
            for md_path in sorted(page_dir.glob("*.md")):
                if md_path.name.startswith("."):
                    continue
                pages.append((directory, md_path.stem, _read_aliases(md_path)))
        return pages

    def _postprocess_summary(
        self,
        summary: str,
        episodes: list[dict[str, Any]],
        slug_index: SlugIndex,
        durable_pages: list[tuple[str, str, list[str]]],
    ) -> tuple[str, list[str], list[str]]:
        """Clean the LLM paragraph and compute its backbone hub links.

        Returns ``(clean_summary, hub_links, resolved_targets)``:

        * ``clean_summary`` — dangling fragments stripped, resolvable links
          canonicalised, unresolvable links demoted to plain text.
        * ``hub_links`` — the durable spine (user entity, most-relevant
          project) every session links into.
        * ``resolved_targets`` — canonical pages the body genuinely references.
        """
        cleaned = strip_dangling_wikilinks(summary)
        cleaned, resolved = rewrite_body_links(cleaned, slug_index)
        hub_links = self._compute_hub_links(slug_index, durable_pages, episodes)
        return cleaned, hub_links, resolved

    def _compute_hub_links(
        self,
        slug_index: SlugIndex,
        durable_pages: list[tuple[str, str, list[str]]],
        episodes: list[dict[str, Any]],
    ) -> list[str]:
        """Pick the durable hubs to link in the footer.

        Always the user entity (when its page exists); plus the single most
        relevant project — chosen by keyword overlap with the session's apps
        and episode summaries, or the sole project when there is exactly one.
        Context-dependent: a project with no overlap and siblings is skipped
        rather than linked at random.
        """
        hubs: list[str] = []

        user_canonical = slug_index.resolve(self._cfg.user_entity_slug)
        if user_canonical:
            hubs.append(user_canonical)

        project_slugs = [slug for d, slug, _a in durable_pages if d == "projects"]
        chosen_project: str | None = None
        if len(project_slugs) == 1:
            chosen_project = project_slugs[0]
        elif project_slugs:
            source = " ".join(
                f"{ep.get('primary_app', '')} {ep.get('summary', '') or ''}"
                for ep in episodes
            )
            ranked = select_top_slugs(source, project_slugs, limit=1)
            chosen_project = ranked[0] if ranked else None
        if chosen_project:
            canonical = slug_index.resolve(chosen_project)
            if canonical and canonical not in hubs:
                hubs.append(canonical)

        return hubs

    def _archive_old_sessions(self) -> list[Path]:
        """Move sessions beyond ``max_active_sessions`` into ``_archive/sessions/``.

        Selection is by filename sort (lexicographic on the
        ``YYYY-MM-DD-<id>`` prefix), oldest first. Returns the list of
        paths actually moved.
        """
        sessions_dir = self._vault_root / "sessions"
        if not sessions_dir.is_dir():
            return []
        files = sorted(
            p for p in sessions_dir.glob("*.md") if p.is_file()
        )
        cap = self._cfg.max_active_sessions
        if len(files) <= cap:
            return []
        archive_dir = self._vault_root / "_archive" / "sessions"
        archive_dir.mkdir(parents=True, exist_ok=True)
        moved: list[Path] = []
        for src in files[: len(files) - cap]:
            dst = archive_dir / src.name
            try:
                src.rename(dst)
                moved.append(dst)
            except OSError as exc:    # noqa: BLE001
                log.warning(
                    "SessionRollupWorker: could not archive %s: %s",
                    src.name, exc,
                )
        return moved

    def _gen_session_id(self) -> str:
        """Eight-char URL-safe random ID, lowercase, no padding."""
        return secrets.token_urlsafe(6).lower().replace("_", "").replace("-", "")[:8] or "session"


# ----------------------------------------------------------------------------
# Time helpers
# ----------------------------------------------------------------------------


def _read_aliases(path: Path) -> list[str]:
    """Return the ``aliases:`` frontmatter values of a page, or ``[]``.

    Tolerant: handles both list form (``aliases: [Ruben, the user]``) and a
    bare single value. A missing or unreadable file yields ``[]`` rather than
    raising — the scan must never crash a rollup.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    match = _ALIASES_RE.search(raw)
    if not match:
        return []
    value = match.group(1).strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [item.strip().strip("\"'") for item in value.split(",") if item.strip()]


def _to_local_date(ts_ns: int) -> str:
    """Render a ns-timestamp as local-time ``YYYY-MM-DD``."""
    if ts_ns <= 0:
        return "0000-00-00"
    return datetime.fromtimestamp(ts_ns / 1_000_000_000).strftime("%Y-%m-%d")


def _to_local_hhmm(ts_ns: int) -> str:
    """Render a ns-timestamp as local-time ``HH:MM``."""
    if ts_ns <= 0:
        return "00:00"
    return datetime.fromtimestamp(ts_ns / 1_000_000_000).strftime("%H:%M")


__all__ = [
    "SessionRollupWorker",
    "SessionRollupResult",
]
