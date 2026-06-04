"""Personal Jarvis — voice-driven, cross-platform meta-orchestrator."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    # Single source of truth: the version declared in pyproject.toml, surfaced
    # via the installed package metadata. Keeps `jarvis --version` in lockstep
    # with the release version instead of a second hardcoded literal.
    __version__ = _pkg_version("personal-jarvis")
except PackageNotFoundError:  # bare checkout without an editable install
    __version__ = "0.0.0"
