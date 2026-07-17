"""Skill registry: in-memory store of all loaded skills + hot reload.

Hot-Reload via watchdog (FileSystemEventHandler + debounce 500ms, asyncio.Lock).
The actual dispatching (which skill triggers on which voice utterance)
happens in the `TriggerMatcher`.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from .loader import discover_skills
from .schema import (
    Skill,
    SkillLifecycleState,
    SkillRegistryReloaded,
)

# watchdog is optional — without it there's no hot reload
try:
    from watchdog.events import FileSystemEventHandler  # type: ignore
    from watchdog.observers import Observer  # type: ignore
    _HAVE_WATCHDOG = True
except Exception:  # pragma: no cover
    FileSystemEventHandler = object  # type: ignore
    Observer = None  # type: ignore
    _HAVE_WATCHDOG = False


def _rewrite_state_in_frontmatter(text: str, new_state: str) -> str:
    """Sets the `state` field in the YAML frontmatter to `new_state`.

    Plan-§7.5-Promote: SKILL.md is edited minimally-invasively, no
    schema re-render — user comments in the body are preserved.

    Sub-agent-review MAJOR hardening: the regex now also matches lines
    with a trailing comment (`state: draft # do not promote`) and a YAML
    anchor (`state: &s draft`). This prevents duplicate keys.
    """
    import re as _re

    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text
    fm = parts[1]
    body = parts[2]
    # `[^\n]*` matches everything up to the next newline — incl. comments + anchors.
    state_re = _re.compile(r"(?m)^state\s*:[^\n]*$")
    if state_re.search(fm):
        fm = state_re.sub(f"state: {new_state}", fm)
    else:
        fm = fm.rstrip() + f"\nstate: {new_state}\n"
    return f"---{fm}---{body}"

log = logging.getLogger(__name__)


class SkillRegistry:
    """Holds all known skills + provides lookup by name/trigger type.

    Thread-safe via ``asyncio.Lock`` for reloads + an internal `threading.Lock`
    for watchdog callbacks (which fire from a different thread).
    """

    def __init__(
        self,
        root: Path,
        bus: Any | None = None,
        debounce_ms: int = 500,
        state_prefs_loader: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self.root = Path(root)
        self.bus = bus
        self._debounce_ms = debounce_ms
        # Optional injection: returns ``{skill_name: "active" | "disabled"}`` —
        # the user's persisted on/off choice, re-applied on every (re)load so a
        # toggle survives restarts. ``None`` → legacy behaviour (no overrides).
        self._state_prefs_loader = state_prefs_loader
        self._skills: dict[str, Skill] = {}
        self._async_lock = asyncio.Lock()
        self._thread_lock = threading.Lock()
        self._observer: Any | None = None
        self._pending_reload: float | None = None   # Unix ts of the next reload
        self._reload_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> Skill:
        if name not in self._skills:
            raise KeyError(f"Skill '{name}' not in registry")
        return self._skills[name]

    def list(self) -> list[Skill]:
        return list(self._skills.values())

    def list_active(self) -> list[Skill]:
        """Phase 7.5 (Plan-§AD-8): skills the TriggerMatcher is allowed to see.

        DRAFT/DISABLED skills are excluded — a hot-reload filter
        against accidental activation of OpenClaw-authored drafts.
        """
        return [
            s
            for s in self._skills.values()
            if s.state in (SkillLifecycleState.ACTIVE, SkillLifecycleState.VALIDATED)
        ]

    def list_drafts(self) -> list[Skill]:
        """Plan-§7.5: all skills with state=DRAFT (produced by the OpenClaw worker
        or flagged by the loader due to a schema error)."""
        return [
            s
            for s in self._skills.values()
            if s.state == SkillLifecycleState.DRAFT
        ]

    def promote(self, slug: str) -> Skill:
        """Plan-§7.5 + Plan-§AP-6: explicit user activation of a draft.

        Phases:
        1. Fetch the skill from drafts.
        2. Security lint of the skill body (no eval/exec/system,
           import allowlist).
        3. Rewrite SKILL.md with `state: active` in the frontmatter.
        4. Reload sync.
        5. Audit entry `skill_promoted`.

        Raises `KeyError` if the slug doesn't exist, `RuntimeError`
        if the skill isn't a DRAFT, `UnsafeSkillError` if the lint
        finds forbidden calls.
        """
        from jarvis.skills.authoring.draft_writer import (
            UnsafeSkillError,
            safe_lint_skill_body,
        )

        # Plan-§7.5: the user CLI calls with a slug (= path.parent.name); SkillRegistry
        # indexes internally by `skill.name`. Fallback lookup via slug.
        skill = self._skills.get(slug)
        if skill is None:
            for candidate in self._skills.values():
                if candidate.path.parent.name == slug:
                    skill = candidate
                    break
        if skill is None:
            raise KeyError(f"Skill '{slug}' not in registry")
        if skill.state != SkillLifecycleState.DRAFT:
            raise RuntimeError(
                f"Skill '{slug}' is not in DRAFT state "
                f"(currently: {skill.state.value})"
            )

        findings = safe_lint_skill_body(skill.body)
        if findings:
            self._record_audit_event(
                error="skill_promote_blocked_unsafe",
                slug=slug,
                extra={"lint_findings": findings},
            )
            raise UnsafeSkillError(
                f"Skill '{slug}' contains disallowed calls: {findings}"
            )

        # Re-write SKILL.md with state=active (Plan-§AD-8: explicit user activation)
        text = skill.path.read_text(encoding="utf-8")
        new_text = _rewrite_state_in_frontmatter(text, "active")
        skill.path.write_text(new_text, encoding="utf-8")

        self.reload_sync()
        promoted = self._skills.get(slug)
        if promoted is None:
            for candidate in self._skills.values():
                if candidate.path.parent.name == slug:
                    promoted = candidate
                    break
        if promoted is None:
            raise RuntimeError(
                f"Promote: skill '{slug}' no longer in registry after reload"
            )

        self._record_audit_event(
            error=None,
            slug=slug,
            extra={"action": "skill_promoted", "previous_state": "draft"},
        )
        return promoted

    def _record_audit_event(
        self,
        *,
        error: str | None,
        slug: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Audit entry via SelfModAudit when available on the bus.

        If no audit adapter is configured: the skill promote is still
        emitted on the bus (SkillStateChanged), which suffices for the trail view.
        """
        try:
            from jarvis.core.self_mod import (
                AuditActor,
                AuditEvent,
                AuditSource,
                SelfModAudit,
            )

            audit = SelfModAudit()
            extras = dict(extra or {})
            audit.record(
                AuditEvent(
                    source=AuditSource.UI,
                    requested_by=AuditActor.USER,
                    path=f"skills.{slug}",
                    old_value=None,
                    new_value=None,
                    ok=error is None,
                    rolled_back=False,
                    error=error,
                    **extras,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("Skill-promote audit fallback (SelfModAudit missing): %s", exc)

    def by_trigger(
        self,
        kind: Literal["voice", "hotkey", "schedule"],
    ) -> list[Skill]:
        out: list[Skill] = []
        for sk in self._skills.values():
            if sk.frontmatter is None:
                continue
            for t in sk.frontmatter.triggers:
                if t.type == kind:
                    out.append(sk)
                    break
        return out

    def needs_setup(self) -> list[Skill]:
        """Skills in DRAFT state (parser or validator error)."""
        return [s for s in self._skills.values() if s.state == SkillLifecycleState.DRAFT]

    # ------------------------------------------------------------------
    # Reload
    # ------------------------------------------------------------------

    def _apply_state_overrides(self, skills: list[Skill]) -> list[Skill]:
        """Overlay the user's persisted on/off choice onto freshly parsed skills.

        AP-15 invariant: a ``DRAFT`` skill is NEVER forced on — only the
        safety-linted ``promote()`` path may activate a draft. A missing override
        leaves the parsed state untouched (a new skill stays VALIDATED = "on").
        """
        if self._state_prefs_loader is None:
            return skills
        try:
            overrides = self._state_prefs_loader()
        except Exception as exc:  # noqa: BLE001 — a broken prefs file must not kill reload
            log.warning("skill state-prefs loader failed: %s", exc)
            return skills
        if not overrides:
            return skills

        out: list[Skill] = []
        for s in skills:
            ov = overrides.get(s.name)
            if ov is None or s.state == SkillLifecycleState.DRAFT:
                out.append(s)
                continue
            if ov == "disabled":
                out.append(replace(s, state=SkillLifecycleState.DISABLED))
            elif ov == "active":
                out.append(replace(s, state=SkillLifecycleState.ACTIVE))
            else:
                out.append(s)
        return out

    def reload_sync(self) -> None:
        """Synchronous reload — for bootstrap + tests."""
        skills = self._apply_state_overrides(discover_skills(self.root))
        with self._thread_lock:
            self._skills = {s.name: s for s in skills}
        self._sync_paired_capabilities()
        self._emit_reloaded()

    async def reload(self) -> None:
        """Async reload with lock."""
        async with self._async_lock:
            skills = await asyncio.get_event_loop().run_in_executor(
                None, discover_skills, self.root
            )
            skills = self._apply_state_overrides(skills)
            with self._thread_lock:
                self._skills = {s.name: s for s in skills}
        self._sync_paired_capabilities()
        self._emit_reloaded()

    def _sync_paired_capabilities(self) -> None:
        """Mirror the paired-skill capabilities after every (re)load.

        The boot registers paired capabilities when the skill context is set,
        but since the serve-first fast boot the disk scan is deferred — that
        registration ran against an EMPTY registry and nothing repaired the
        capability surface afterwards, so the evidence gate refused connected
        plugin domains for the whole session (live 2026-07-17). Hooking the
        sync into the registry's own reload covers every load source with one
        code path: the deferred boot scan, the watchdog hot reload, and the
        explicit reload_sync callers. Best-effort by design — a capability
        fault must never break a skill reload.
        """
        try:
            from jarvis.core.capabilities import get_registry
            from jarvis.skills.plugin_coupling import sync_paired_capabilities

            sync_paired_capabilities(get_registry(), self.list())
        except Exception:  # noqa: BLE001
            log.debug("paired-capability sync failed", exc_info=True)

    def _emit_reloaded(self) -> None:
        if self.bus is None:
            return
        total = len(self._skills)
        active = sum(
            1 for s in self._skills.values() if s.state == SkillLifecycleState.ACTIVE
        )
        draft = sum(
            1 for s in self._skills.values() if s.state == SkillLifecycleState.DRAFT
        )
        evt = SkillRegistryReloaded(total=total, active=active, draft=draft)
        # bus.publish is async — fire and forget
        try:
            loop = self._loop or asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(self.bus.publish(evt), loop)
        except RuntimeError:  # pragma: no cover
            pass

    # ------------------------------------------------------------------
    # Hot-Reload (watchdog)
    # ------------------------------------------------------------------

    def start_watcher(self, loop: asyncio.AbstractEventLoop | None = None) -> bool:
        """Starts the filesystem watcher (if watchdog is installed).

        Returns True on success, False if watchdog is missing or the root
        doesn't exist.
        """
        if not _HAVE_WATCHDOG:
            log.info("watchdog not installed — no hot reload")
            return False
        if not self.root.exists():
            log.warning("skill root does not exist: %s", self.root)
            return False

        self._loop = loop or asyncio.get_event_loop()

        registry_self = self

        class _Handler(FileSystemEventHandler):  # type: ignore[misc]
            def on_any_event(self, event: Any) -> None:  # noqa: D401
                if getattr(event, "is_directory", False):
                    return
                src = getattr(event, "src_path", "")
                if not str(src).endswith(".md"):
                    return
                registry_self._schedule_reload()

        observer = Observer()  # type: ignore[operator]
        observer.schedule(_Handler(), str(self.root), recursive=True)
        observer.daemon = True
        observer.start()
        self._observer = observer
        return True

    def stop_watcher(self) -> None:
        if self._observer is None:
            return
        try:
            self._observer.stop()
            self._observer.join(timeout=2.0)
        except Exception:  # pragma: no cover
            pass
        self._observer = None

    def _schedule_reload(self) -> None:
        """Debounce mechanism: on rapidly successive FS events, we
        wait `debounce_ms` before actually reloading."""
        deadline = time.monotonic() + self._debounce_ms / 1000.0
        with self._thread_lock:
            self._pending_reload = deadline
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._debounced_reload(), self._loop)
        except RuntimeError:  # pragma: no cover
            pass

    async def _debounced_reload(self) -> None:
        await asyncio.sleep(self._debounce_ms / 1000.0)
        with self._thread_lock:
            deadline = self._pending_reload
            self._pending_reload = None
        if deadline is None:
            return
        # If a newer reload was scheduled in the meantime, skip
        if time.monotonic() + 0.001 < deadline:
            return
        try:
            await self.reload()
        except Exception as exc:  # noqa: BLE001
            log.exception("skill reload failed: %s", exc)
