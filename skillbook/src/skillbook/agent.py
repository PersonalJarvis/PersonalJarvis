"""AgentInstance: the composing layer.

Wires memory_layer + guardrails + ace_core + p2p_sync + symcon_bridge into a
single object the capstone scenario drives. The agent's :meth:`run_task` runs
the Generator path, and on a guardrail-blocked outcome it invokes the
RecursiveReflector and Curator to learn a corrective skillbook rule.

Per ADR-0010 every adapter — ``llm``, ``actors``, ``transport`` — is a
required keyword argument of :meth:`build`; production factories do not fall
back to in-tree test doubles. Missing values raise :class:`MissingAdapterError`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from skillbook.ace_core.curator import Curator
from skillbook.ace_core.generator import Actor, Generator
from skillbook.ace_core.llm import LLM
from skillbook.ace_core.models import Task, TaskResult, TaskStatus
from skillbook.ace_core.reflector import RecursiveReflector
from skillbook.errors import MissingAdapterError
from skillbook.guardrails.diagnostics import AgentDoG
from skillbook.guardrails.lats import CircuitBreaker, LATSEngine
from skillbook.memory_layer.embedder import Embedder, HashEmbedder
from skillbook.memory_layer.store import SQLiteMemoryStore
from skillbook.p2p_sync.engine import SyncEngine
from skillbook.p2p_sync.transport import Transport


async def _instant_sleep(_: float) -> None:
    return None


@dataclass(slots=True)
class AgentInstance:
    peer_id: str
    memory: SQLiteMemoryStore
    generator: Generator
    reflector: RecursiveReflector
    curator: Curator
    engine: LATSEngine
    sync: SyncEngine

    @classmethod
    async def build(
        cls,
        *,
        peer_id: str,
        db_path: Path | str,
        llm: LLM,
        actors: Sequence[Actor],
        transport: Transport,
        embedder: Embedder | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
        breaker_max_attempts: int = 5,
        reflector_timeout_s: float = 8.0,
    ) -> "AgentInstance":
        if llm is None:
            raise MissingAdapterError(
                "llm",
                hint="Pass the deterministic fake from tests/fakes/ in tests or skillbook.ace_core.llm.default_llm() in production.",
            )
        if actors is None:
            raise MissingAdapterError(
                "actors",
                hint="Pass an explicit Sequence of actors; an empty tuple () is acceptable.",
            )
        if transport is None:
            raise MissingAdapterError(
                "transport",
                hint="Pass the in-process fake transport from tests/fakes/ in tests or a real transport implementation in production.",
            )

        memory = SQLiteMemoryStore(db_path=db_path)
        await memory.open()
        engine = LATSEngine(
            dog=AgentDoG(),
            breaker=CircuitBreaker(max_attempts=breaker_max_attempts),
        )
        generator = Generator(
            memory=memory,
            engine=engine,
            sleep_fn=sleep_fn if sleep_fn is not None else _instant_sleep,
        )
        reflector = RecursiveReflector(
            memory=memory,
            llm=llm,
            timeout_s=reflector_timeout_s,
        )
        curator = Curator(
            memory=memory,
            embedder=embedder if embedder is not None else HashEmbedder(),
            peer_id=peer_id,
        )
        sync = SyncEngine(memory=memory, transport=transport, peer_id=peer_id)
        await sync.start()

        instance = cls(
            peer_id=peer_id,
            memory=memory,
            generator=generator,
            reflector=reflector,
            curator=curator,
            engine=engine,
            sync=sync,
        )
        for actor in actors:
            instance.register_actor(actor)
        return instance

    def register_actor(self, actor: Actor) -> None:
        self.generator.register_actor(actor)

    async def run_task(self, task: Task) -> TaskResult:
        result = await self.generator.run_task(task)
        if result.status is TaskStatus.BLOCKED_BY_GUARDRAIL:
            verdict = await self.reflector.reflect(task_id=task.id)
            await self.curator.curate(verdict)
        return result

    async def sync_once(self) -> None:
        await self.sync.sync_once()

    async def close(self) -> None:
        await self.memory.close()
