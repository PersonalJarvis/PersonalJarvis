"""Personal Jarvis — a voice-driven, cross-platform meta-orchestrator."""

__version__ = "2.9.0"

# Every httpx client in the process shares one parsed CA bundle instead of
# re-parsing it (~200 ms of CPU) per client. Patches httpx lazily, the moment
# it is first imported; importing ``jarvis`` itself still loads nothing heavy.
from jarvis.core.shared_tls import install_when_imported as _install_shared_tls

_install_shared_tls()
del _install_shared_tls
