"""Skill-Registry: In-Memory-Store aller geladenen Skills + Hot-Reload.

Hot-Reload via watchdog (FileSystemEventHandler + debounce 500ms, asyncio.Lock).
Das eigentliche Dispatching (welcher Skill triggert auf welche Voice-Utterance)
passiert im `TriggerMatcher`.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any, Literal

from .loader import discover_skills
from .schema import (
    Skill,
    SkillLifecycleState,
    SkillRegistryReloaded,
)

# watchdog ist optional — wenn es fehlt, gibt's keinen Hot-Reload
try:
    from watchdog.events import FileSystemEventHandler  # type: ignore
    from watchdog.observers import Observer  # type: ignore
    _HAVE_WATCHDOG = True
except Exception:  # pragma: no cover
    FileSystemEventHandler = object  # type: ignore
    Observer = None  # type: ignore
    _HAVE_WATCHDOG = False


def _rewrite_state_in_frontmatter(text: str, new_state: str) -> str:
    """Setzt das `state`-Field im YAML-Frontmatter auf `new_state`.

    Plan-§7.5-Promote: SKILL.md wird minimal-invasiv editiert, kein
    Schema-Re-Render — User-Kommentare im Body bleiben erhalten.

    Sub-Agent-Review-MAJOR-Hardening: Regex matcht jetzt auch Zeilen
    mit Trailing-Comment (`state: draft # do not promote`) und YAML-
    Anchor (`state: &s draft`). Das verhindert Duplikat-Keys.
    """
    import re as _re

    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text
    fm = parts[1]
    body = parts[2]
    # `[^\n]*` matcht alles bis zum nächsten Newline — inkl. Comments + Anchors.
    state_re = _re.compile(r"(?m)^state\s*:[^\n]*$")
    if state_re.search(fm):
        fm = state_re.sub(f"state: {new_state}", fm)
    else:
        fm = fm.rstrip() + f"\nstate: {new_state}\n"
    return f"---{fm}---{body}"

log = logging.getLogger(__name__)


class SkillRegistry:
    """Hält alle bekannten Skills + bietet Lookup nach Name/Trigger-Typ.

    Thread-safe via ``asyncio.Lock`` für Reloads + interner `threading.Lock`
    für watchdog-Callbacks (die aus einem anderen Thread feuern).
    """

    def __init__(
        self,
        root: Path,
        bus: Any | None = None,
        debounce_ms: int = 500,
    ) -> None:
        self.root = Path(root)
        self.bus = bus
        self._debounce_ms = debounce_ms
        self._skills: dict[str, Skill] = {}
        self._async_lock = asyncio.Lock()
        self._thread_lock = threading.Lock()
        self._observer: Any | None = None
        self._pending_reload: float | None = None   # Unix-ts des nächsten Reloads
        self._reload_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> Skill:
        if name not in self._skills:
            raise KeyError(f"Skill '{name}' nicht im Registry")
        return self._skills[name]

    def list(self) -> list[Skill]:
        return list(self._skills.values())

    def list_active(self) -> list[Skill]:
        """Phase 7.5 (Plan-§AD-8): Skills, die der TriggerMatcher sehen darf.

        DRAFT/DISABLED-Skills sind ausgeschlossen — Hot-Reload-Filter
        gegen versehentliche Aktivierung von OpenClaw-authored Drafts.
        """
        return [
            s
            for s in self._skills.values()
            if s.state in (SkillLifecycleState.ACTIVE, SkillLifecycleState.VALIDATED)
        ]

    def list_drafts(self) -> list[Skill]:
        """Plan-§7.5: alle Skills mit state=DRAFT (vom OpenClaw-Worker erzeugt
        oder vom Loader wegen Schema-Fehler markiert)."""
        return [
            s
            for s in self._skills.values()
            if s.state == SkillLifecycleState.DRAFT
        ]

    def promote(self, slug: str) -> Skill:
        """Plan-§7.5 + Plan-§AP-6: User-explizite Aktivierung eines Drafts.

        Phasen:
        1. Skill aus Drafts holen.
        2. Sicherheits-Lint des Skill-Bodies (kein eval/exec/system,
           Import-Allowlist).
        3. SKILL.md neu schreiben mit `state: active` im Frontmatter.
        4. Reload-Sync.
        5. Audit-Eintrag `skill_promoted`.

        Wirft `KeyError` wenn der Slug nicht existiert, `RuntimeError`
        wenn der Skill kein DRAFT ist, `UnsafeSkillError` wenn der Lint
        verbotene Calls findet.
        """
        from jarvis.skills.authoring.draft_writer import (
            UnsafeSkillError,
            safe_lint_skill_body,
        )

        # Plan-§7.5: User-CLI ruft mit Slug (= path.parent.name); SkillRegistry
        # indiziert intern nach `skill.name`. Fallback-Lookup über Slug.
        skill = self._skills.get(slug)
        if skill is None:
            for candidate in self._skills.values():
                if candidate.path.parent.name == slug:
                    skill = candidate
                    break
        if skill is None:
            raise KeyError(f"Skill '{slug}' nicht im Registry")
        if skill.state != SkillLifecycleState.DRAFT:
            raise RuntimeError(
                f"Skill '{slug}' ist nicht im DRAFT-Zustand "
                f"(aktuell: {skill.state.value})"
            )

        findings = safe_lint_skill_body(skill.body)
        if findings:
            self._record_audit_event(
                error="skill_promote_blocked_unsafe",
                slug=slug,
                extra={"lint_findings": findings},
            )
            raise UnsafeSkillError(
                f"Skill '{slug}' enthält unerlaubte Calls: {findings}"
            )

        # SKILL.md mit state=active re-write (Plan-§AD-8: User-explizite Aktivierung)
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
                f"Promote: Skill '{slug}' nach reload nicht mehr im Registry"
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
        """Audit-Eintrag via SelfModAudit wenn auf dem Bus verfügbar.

        Falls kein Audit-Adapter konfiguriert: Skill-Promote wird auf den
        Bus emittiert (SkillStateChanged), reicht für die Trail-Sicht.
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
            log.debug("Skill-Promote-Audit-Fallback (SelfModAudit fehlt): %s", exc)

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
        """Skills im DRAFT-State (Parser- oder Validator-Fehler)."""
        return [s for s in self._skills.values() if s.state == SkillLifecycleState.DRAFT]

    # ------------------------------------------------------------------
    # Reload
    # ------------------------------------------------------------------

    def reload_sync(self) -> None:
        """Synchroner Reload — für Bootstrap + Tests."""
        skills = discover_skills(self.root)
        with self._thread_lock:
            self._skills = {s.name: s for s in skills}
        self._emit_reloaded()

    async def reload(self) -> None:
        """Async-Reload mit Lock."""
        async with self._async_lock:
            skills = await asyncio.get_event_loop().run_in_executor(
                None, discover_skills, self.root
            )
            with self._thread_lock:
                self._skills = {s.name: s for s in skills}
        self._emit_reloaded()

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
        # bus.publish ist async — feuern und vergessen
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
        """Startet den Filesystem-Watcher (falls watchdog installiert).

        Returns True bei Erfolg, False wenn watchdog nicht vorhanden oder Root
        nicht existiert.
        """
        if not _HAVE_WATCHDOG:
            log.info("watchdog nicht installiert — kein Hot-Reload")
            return False
        if not self.root.exists():
            log.warning("skill-root existiert nicht: %s", self.root)
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
        """Debounce-Mechanismus: bei schnell aufeinanderfolgenden FS-Events
        warten wir `debounce_ms`, bevor wir tatsächlich reloaden."""
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
        # Wenn zwischenzeitlich ein neuerer Reload geplant wurde, skippen
        if time.monotonic() + 0.001 < deadline:
            return
        try:
            await self.reload()
        except Exception as exc:  # noqa: BLE001
            log.exception("skill reload failed: %s", exc)
