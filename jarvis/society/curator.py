"""Event-driven curator for durable society RESULT knowledge.

The board stays authoritative. A curator never writes shared knowledge directly:
it stages a reviewed:false page in the producing agent's namespace and creates the
existing human approval that may promote that page into society/shared/.

No second model is required here. RESULT is already a structured handoff; this
component deterministically compacts that record to a bounded candidate and
preserves the source event for crash-safe replay.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Final

from .events import MsgType, SocietyEnvelope
from .memory import MEMORY_SHARE_CAPABILITY, MemoryRefused

if TYPE_CHECKING:
    from .runtime import SocietyRuntime

log = logging.getLogger(__name__)

__all__ = ["Curator"]

_RECOVERY_KEY: Final[str] = "curator:recovery_seq"
_MAX_WORDS: Final[int] = 150
_INTERNAL_OUTPUT_PREFIXES: Final[tuple[str, ...]] = ("chat:", "turn:")


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _clip_words(text: str, limit: int = _MAX_WORDS) -> str:
    words = text.split()
    if len(words) <= limit:
        return " ".join(words)
    return " ".join(words[:limit]) + " …"


class Curator:
    """Stage share candidates from validated RESULT events, idempotently."""

    def __init__(self, runtime: SocietyRuntime) -> None:
        self._runtime = runtime
        self._lock = asyncio.Lock()

    async def recover(self) -> None:
        """Replay RESULTs committed after the previous clean recovery boundary.

        On the first deployment we establish a floor at the current tail rather
        than flooding the person with approvals for historical work. Live
        RESULTs are handled by the scheduler; the next startup replays the
        interval since this durable boundary and repairs any commit/publish gap.
        """
        raw = await self._runtime.store.get_meta(_RECOVERY_KEY, "")
        if not raw:
            await self._runtime.store.set_meta(
                _RECOVERY_KEY, str(await self._runtime.store.last_seq())
            )
            return
        try:
            after = max(0, int(raw))
        except ValueError:
            log.warning("society curator: invalid recovery cursor %r; replaying from zero", raw)
            after = 0
        while True:
            events = await self._runtime.store.events_since(after, limit=500)
            if not events:
                return
            for env in events:
                if env.msg_type is MsgType.RESULT:
                    await self.on_result(env)
                if env.seq is not None:
                    after = int(env.seq)
            await self._runtime.store.set_meta(_RECOVERY_KEY, str(after))

    async def on_result(self, env: SocietyEnvelope) -> None:
        if env.msg_type is not MsgType.RESULT:
            return
        async with self._lock:
            await self._curate_locked(env)

    async def _curate_locked(self, env: SocietyEnvelope) -> None:
        candidate = self._candidate(env)
        if candidate is None:
            return
        title, text, origin = candidate
        existing = await self._runtime.store.knowledge_for_source_event(env.event_id)
        if existing is not None:
            await self._ensure_approval(existing, env, title)
            return
        agent = await self._runtime.roster.get(env.from_agent)
        if agent is None:
            log.info("society curator: result %s has no roster owner", env.event_id)
            return
        try:
            await self._runtime.memory.propose_shared(
                agent,
                title,
                text,
                origin=origin,
                trace=env.trace_id,
                source_event=env.event_id,
            )
        except MemoryRefused as exc:
            # Secret detection and path containment are intentional terminal gates.
            log.info("society curator: result %s not staged: %s", env.event_id, exc)

    async def _ensure_approval(
        self, row: dict[str, Any], env: SocietyEnvelope, title: str
    ) -> None:
        knowledge_id = int(row["id"])
        for item in await self._runtime.approvals.items():
            if (
                item.capability == MEMORY_SHARE_CAPABILITY
                and int(item.action.get("knowledge_id") or 0) == knowledge_id
            ):
                return
        await self._runtime.approvals.enqueue(
            agent_id=str(row["agent_id"]),
            trace_id=env.trace_id or f"memory:{row['agent_id']}",
            capability=MEMORY_SHARE_CAPABILITY,
            action={
                "knowledge_id": knowledge_id,
                "path": str(row["wiki_path"]),
                "title": title,
                "source_event": env.event_id,
            },
            summary=f"Promote to shared knowledge: {title}",
        )

    @staticmethod
    def _candidate(env: SocietyEnvelope) -> tuple[str, str, str] | None:
        payload = env.payload
        if str(payload.get("status") or "done") != "done":
            return None
        done = str(payload.get("done") or "").strip()
        if not done:
            return None
        explicit = payload.get("curate")
        if explicit is False:
            return None
        outputs = _strings(payload.get("output"))
        evidence = _strings(payload.get("evidence"))
        origin_raw = str(payload.get("origin") or "agent").strip().lower()
        origin = origin_raw if origin_raw in {"web", "tool", "agent"} else "agent"
        durable_output = any(
            not item.startswith(_INTERNAL_OUTPUT_PREFIXES) for item in outputs
        )
        if explicit is not True and origin == "agent" and not evidence and not durable_output:
            return None

        title_seed = done.splitlines()[0].strip()
        if len(title_seed) > 90:
            title_seed = title_seed[:87].rstrip() + "..."
        title = f"Result: {title_seed}"
        parts = [f"Done: {done}"]
        if outputs:
            parts.append("Output: " + "; ".join(outputs))
        if evidence:
            parts.append("Evidence: " + "; ".join(evidence))
        open_items = _strings(payload.get("open"))
        if open_items:
            parts.append("Open: " + "; ".join(open_items))
        return title, _clip_words("\n".join(parts)), origin
