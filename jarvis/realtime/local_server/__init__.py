"""Managed local realtime server: install, patch, and readiness machinery.

The self-hosted speech-to-speech server behind the ``local-realtime``
provider graduates here from a hand-built experiment to a Jarvis-managed
runtime (docs/plans/local-realtime-one-click-install-plan.md). The package
owns everything a non-maintainer install needs: the vendored upstream patch
(:mod:`.patching`), and — in later chunks — preflight, tier selection, the
install engine, and fail-closed readiness reporting.
"""

from jarvis.realtime.local_server.patching import (
    PatchDriftError,
    apply_create_response_patch,
    patch_state,
)

__all__ = [
    "PatchDriftError",
    "apply_create_response_patch",
    "patch_state",
]

# The heavier surfaces (preflight, install engine, tier ladder, brain
# resolution) are imported from their own modules on purpose — routes and
# CLI import them lazily so nothing here rides the boot critical path.
