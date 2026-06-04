"""Memory-Layer: Recall (FTS5) + Core-Memory (JSON) + Workspace + Auto-Recording.

Workspace (neu, OpenClaw-inspired):
- `UserProfile` fuer USER.md (strukturiertes Persoenlichkeits-Profil)
- `Soul` fuer SOUL.md (Jarvis' eigene Persona)
- `PersonStore` fuer people/<name>.md (Menschen im Umfeld — getrennt vom User)
- `Curator` fuer LLM-getriebene Extraktion + Validierung + Merge
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
