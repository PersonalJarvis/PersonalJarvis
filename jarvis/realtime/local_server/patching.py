"""Checksum-guarded application of the vendored local-realtime server patch.

The managed install pins ``speech-to-speech==0.2.12`` whose realtime API
ignores the OpenAI protocol's manual-response mode (``turn_detection.
create_response: false``): every final transcription triggered an LLM
generation plus TTS that a manual-mode client then cancelled as unsolicited,
doubling LLM load per turn. The fix lives as a unified diff next to this
module (``patches/service_create_response_0.2.12.patch``, upstream
candidate) and is applied after every install.

Fail-closed by design (AD-3): the diff is only ever applied to the
byte-exact pristine 0.2.12 file, verified by SHA-256 before and after. Any
drift — a different upstream version, a hand-edited file, a partially
written install — raises :class:`PatchDriftError` instead of guessing, so a
pin bump can never silently ship the unpatched double-generation behavior.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

log = logging.getLogger(__name__)

#: The upstream version this patch targets. A pin bump MUST regenerate the
#: diff and both hashes together (guarded by tests + the install smoke).
PATCH_TARGET_VERSION = "0.2.12"

#: sha256 of the pristine service.py inside the 0.2.12 wheel from PyPI.
PRISTINE_SHA256 = "31b622c287c946a9604abd56e3531a898084cbd9ab7f53292db9d7146c63a7c2"

#: sha256 of service.py after this patch applied — the only accepted result.
PATCHED_SHA256 = "17aa3f65b5ae434a5b33e149ab0994e98a5f372128763bcf2a54d180b3144d4c"

#: Path of the patched file relative to a venv's site-packages directory.
SERVICE_RELPATH = Path("speech_to_speech") / "api" / "openai_realtime" / "service.py"

_PATCH_FILE = (
    Path(__file__).parent / "patches" / f"service_create_response_{PATCH_TARGET_VERSION}.patch"
)

# Pocket TTS 2.1 added current multilingual checkpoints, while the pinned
# speech-to-speech handler still always loads its English default. Jarvis uses
# an internal ``@language:voice`` token in the existing voice argument so the
# patch remains local to one handler and introduces no new CLI enum layer.
POCKET_HANDLER_RELPATH = Path("speech_to_speech") / "TTS" / "pocket_tts_handler.py"
POCKET_HANDLER_PRISTINE_SHA256 = "72e5c24cb0d4540d98c60276cee2de8950cf106361948569fe558e683200e62d"
# Filled from the byte-exact patch result below; pinned by tests.
POCKET_HANDLER_PATCHED_SHA256 = "58ec72c68c14927473a18fccbea2e7b7e51166c55a53122ab953f866e595238e"
_POCKET_PATCH_FILE = (
    Path(__file__).parent / "patches" / f"pocket_language_{PATCH_TARGET_VERSION}.patch"
)


class PatchDriftError(RuntimeError):
    """The target file matches neither the pristine nor the patched hash."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _apply_unified_diff(original: str, diff_text: str) -> str:
    """Apply a single-file unified diff to ``original`` and return the result.

    Deliberately minimal: no fuzz, no offsets — a context mismatch raises.
    That strictness is safe here because the caller only ever reaches this
    function after the SHA-256 gate proved the input is the byte-exact file
    the diff was generated from.
    """
    src = original.splitlines(keepends=True)
    out: list[str] = []
    cursor = 0
    for raw_line in diff_text.splitlines(keepends=True):
        if raw_line.startswith(("--- ", "+++ ")):
            continue
        if raw_line.startswith("@@"):
            header = raw_line.split("@@")[1].strip()
            old_start = int(header.split(" ")[0].lstrip("-").split(",")[0])
            hunk_start = old_start - 1
            out.extend(src[cursor:hunk_start])
            cursor = hunk_start
            continue
        body = raw_line[1:]
        if raw_line.startswith("+"):
            out.append(body)
        elif raw_line.startswith("-"):
            if src[cursor].rstrip("\r\n") != body.rstrip("\r\n"):
                raise PatchDriftError("hunk context mismatch on a removed line")
            cursor += 1
        elif raw_line.startswith((" ", "\n")):
            if src[cursor].rstrip("\r\n") != body.rstrip("\r\n"):
                raise PatchDriftError("hunk context mismatch on a context line")
            out.append(src[cursor])
            cursor += 1
        # Anything else ("\\ No newline at end of file", index lines) is
        # metadata this single-file diff does not contain; ignore defensively.
    out.extend(src[cursor:])
    return "".join(out)


def patch_state(site_packages: Path) -> str:
    """Report the patch state of an installed server, without touching it.

    Returns ``"pristine"``, ``"patched"``, ``"missing"`` or ``"drifted"``.
    Read-only and network-free, so status probes may call it freely.
    """
    target = site_packages / SERVICE_RELPATH
    try:
        digest = _sha256(target.read_bytes())
    except OSError:
        # Reported through the documented "missing" return state, not a log call.
        return "missing"
    if digest == PRISTINE_SHA256:
        return "pristine"
    if digest == PATCHED_SHA256:
        return "patched"
    return "drifted"


def apply_create_response_patch(site_packages: Path) -> str:
    """Apply the vendored patch to a freshly installed server.

    Returns ``"applied"`` or ``"already-applied"``. Raises
    :class:`PatchDriftError` for a missing or drifted target and never
    writes anything in that case.
    """
    target = site_packages / SERVICE_RELPATH
    state = patch_state(site_packages)
    if state == "patched":
        return "already-applied"
    if state != "pristine":
        raise PatchDriftError(
            f"{target} is {state}: expected the pristine speech-to-speech "
            f"{PATCH_TARGET_VERSION} file (sha256 {PRISTINE_SHA256[:12]}…). "
            "A pin bump must regenerate the vendored patch and hashes "
            "together; refusing to modify an unknown file."
        )
    # Bytes in, bytes out: text-mode IO would translate line endings and
    # silently break the byte-exact result hash below.
    original = target.read_bytes().decode("utf-8")
    diff_text = _PATCH_FILE.read_bytes().decode("utf-8")
    patched = _apply_unified_diff(original, diff_text)
    if _sha256(patched.encode("utf-8")) != PATCHED_SHA256:
        raise PatchDriftError(
            "patch application produced an unexpected result hash; the "
            "vendored diff and its recorded hashes are out of sync"
        )
    target.write_bytes(patched.encode("utf-8"))
    log.info("local-realtime: applied create_response patch to %s", target)
    return "applied"


def pocket_language_patch_state(site_packages: Path) -> str:
    """Report whether the Pocket TTS multilingual compatibility patch is ready."""
    target = site_packages / POCKET_HANDLER_RELPATH
    try:
        digest = _sha256(target.read_bytes())
    except OSError:
        # Reported through the documented "missing" return state, not a log call.
        return "missing"
    if digest == POCKET_HANDLER_PRISTINE_SHA256:
        return "pristine"
    if digest == POCKET_HANDLER_PATCHED_SHA256:
        return "patched"
    return "drifted"


def apply_pocket_language_patch(site_packages: Path) -> str:
    """Enable Pocket TTS 2.1 language profiles in the pinned server handler."""
    target = site_packages / POCKET_HANDLER_RELPATH
    state = pocket_language_patch_state(site_packages)
    if state == "patched":
        return "already-applied"
    if state != "pristine":
        raise PatchDriftError(
            f"{target} is {state}: expected the pristine speech-to-speech "
            f"{PATCH_TARGET_VERSION} Pocket handler; refusing to modify an unknown file"
        )
    original = target.read_bytes().decode("utf-8")
    diff_text = _POCKET_PATCH_FILE.read_bytes().decode("utf-8")
    patched = _apply_unified_diff(original, diff_text)
    if _sha256(patched.encode("utf-8")) != POCKET_HANDLER_PATCHED_SHA256:
        raise PatchDriftError(
            "Pocket language patch produced an unexpected result hash; the "
            "vendored diff and its recorded hashes are out of sync"
        )
    target.write_bytes(patched.encode("utf-8"))
    log.info("local-realtime: applied Pocket language patch to %s", target)
    return "applied"
