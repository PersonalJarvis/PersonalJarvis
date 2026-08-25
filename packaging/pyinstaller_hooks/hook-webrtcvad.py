# Overrides the pyinstaller-hooks-contrib hook for `webrtcvad`.
#
# The contrib hook calls `copy_metadata('webrtcvad')` unconditionally. Personal
# Jarvis installs `webrtcvad-wheels` — the maintained fork that ships binary
# wheels, so no compiler is needed on any OS — which provides the SAME
# `webrtcvad` import name under a DIFFERENT distribution name. `copy_metadata`
# then raises, and PyInstaller aborts the whole build while importing the hook
# ("Failed to import module __PyInstaller_hooks_0_webrtcvad").
#
# User hook directories outrank the contrib ones (HOOK_PRIORITY_USER_HOOKS), so
# this file replaces it. Both distribution names are tried, and neither being
# present is fine: the metadata is not needed at runtime, only the module is.

from PyInstaller.utils.hooks import copy_metadata


def _metadata_of_first_installed(*distributions: str) -> list:
    """Metadata of the first distribution that is actually installed.

    An absent distribution is the expected case, not an error worth reporting:
    only one of the two names is ever present, and neither being present is
    fine because the module - not its metadata - is what the app imports.
    """
    for distribution in distributions:
        try:
            return copy_metadata(distribution)
        except Exception as exc:  # noqa: BLE001 - any resolution failure means "not this one"
            print(f"[hook-webrtcvad] {distribution}: {exc}")
    return []


datas = _metadata_of_first_installed("webrtcvad-wheels", "webrtcvad")
