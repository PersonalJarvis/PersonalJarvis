"""Memory layer: recall (FTS5) + core memory (JSON) + workspace + auto-recording.

Workspace (new, Jarvis-Agent-inspired):
- `UserProfile` for USER.md (structured personality profile)
- `Soul` for SOUL.md (Jarvis' own persona)
- `PersonStore` for people/<name>.md (people around the user — kept separate from the user)
- `Curator` for LLM-driven extraction + validation + merge
"""
from __future__ import annotations

from .bootstrap_runner import BootstrapRunner
from .core_memory import CORE_MEMORY_FILENAME, CoreMemory, default_core_memory
from .message_recorder import MessageRecorder
from .people import Person, PersonStore
from .recall import RecallStore
from .soul import Soul
from .user_profile import UserProfile
from .workspace import Workspace, person_slug

__all__ = [
    "BootstrapRunner",
    "CORE_MEMORY_FILENAME",
    "CoreMemory",
    "MessageRecorder",
    "Person",
    "PersonStore",
    "RecallStore",
    "Soul",
    "UserProfile",
    "Workspace",
    "default_core_memory",
    "person_slug",
]
