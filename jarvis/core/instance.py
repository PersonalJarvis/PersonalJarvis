"""Which *instance* of the desktop app this process is.

One checkout can run two desktop apps side by side: the **default** instance —
the one that owns the user's day (voice, hotkeys, chat channels, autostart) and
hosts the long-lived Agentic-IDE sessions — and a **dev** instance that is
started and restarted freely to look at the current working tree without
tearing those sessions down. Both load the same ``jarvis.toml``, the same
credentials and the same per-user agent accounts / skills; everything that two
processes cannot share is separated here:

* the runtime data directory (SQLite stores, single-instance lock, WebView
  profile, logs) — ``data/`` vs ``data-dev/`` next to the checkout;
* the listening ports — the dev instance sits ``DEV_PORT_OFFSET`` above every
  configured port;
* the OS identity — window title, taskbar/dock icon (a DEV-badged copy), the
  Windows AppUserModelID / Start-Menu shortcut / branded launcher exe, so the two
  apps never group under one taskbar button and never focus each other's window;
* the *ambient duties* — only the default instance arms the wake word, the
  global hotkeys, the chat channels, reconciles autostart and draws the
  on-screen overlay (Jarvis Bar / orb). Two apps answering "Hey Jarvis" at
  once — or two bars stacked on one spot of the screen — is a defect, not a
  feature.

The instance is chosen by the ``JARVIS_INSTANCE`` environment variable (the
launcher's ``--instance`` flag sets it before anything else is imported). It is
deliberately a *single-underscore* variable: the relauncher drops every
``JARVIS__*`` config override on an in-app restart (``fresh_user_env``), and the
instance must survive exactly that restart — a dev app that came back as the
default app would bounce off the live one's lock.

Standard-library only and free of filesystem / network I/O at import time, like
``jarvis.core.branding``: the fast-boot path, the onboarding state module and
the shortcut installer all read it before the heavy config module loads.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from jarvis.core.branding import (
    LINUX_DESKTOP_ENTRY_FILE_NAME,
    LINUX_WM_CLASS,
    MACOS_BUNDLE_ID,
    PRODUCT_COMPACT_NAME,
    PRODUCT_NAME,
    PRODUCT_SLUG,
    WINDOWS_APP_USER_MODEL_ID,
    WINDOWS_BRANDED_LAUNCHER_FILE_NAME,
    WINDOWS_MUTEX_NAME,
    WINDOWS_SHORTCUT_FILE_NAME,
)

#: Environment variable naming the instance. Absent / blank = the default app.
INSTANCE_ENV_VAR = "JARVIS_INSTANCE"
DEFAULT_INSTANCE_NAME = "default"
DEV_INSTANCE_NAME = "dev"
#: The dev instance listens this many ports above every configured port.
DEV_PORT_OFFSET = 100
#: Icon file (in ``assets/icons``) each instance shows on taskbar, dock and tray.
DEFAULT_ICON_FILE_NAME = "jarvis.ico"
DEV_ICON_FILE_NAME = "jarvis-dev.ico"
#: Short label appended to the product name ("Personal Jarvis Dev").
DEV_LABEL = "Dev"

KNOWN_INSTANCE_NAMES = (DEFAULT_INSTANCE_NAME, DEV_INSTANCE_NAME)

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,23}$")


class InstanceNameError(ValueError):
    """``JARVIS_INSTANCE`` names an instance this build does not know."""


def normalize_instance_name(raw: str | None) -> str:
    """Canonical instance name for a raw env/CLI value.

    ``None``, blank and ``"default"`` (any case) all mean the default instance.
    Anything else must be one of :data:`KNOWN_INSTANCE_NAMES`; a typo must fail
    loudly here rather than silently boot a second *default* app that fights the
    live one over its lock and port.
    """
    name = (raw or "").strip().lower()
    if not name:
        return DEFAULT_INSTANCE_NAME
    if not _NAME_RE.match(name) or name not in KNOWN_INSTANCE_NAMES:
        known = ", ".join(KNOWN_INSTANCE_NAMES)
        raise InstanceNameError(
            f"{INSTANCE_ENV_VAR}={raw!r} is not a known instance (known: {known})"
        )
    return name


@dataclass(frozen=True, slots=True)
class InstanceIdentity:
    """Everything that differs between the instances, resolved once."""

    name: str

    # ---- flags ---------------------------------------------------------
    @property
    def is_default(self) -> bool:
        return self.name == DEFAULT_INSTANCE_NAME

    @property
    def is_dev(self) -> bool:
        return self.name == DEV_INSTANCE_NAME

    @property
    def owns_ambient_duties(self) -> bool:
        """May this instance arm the wake word, global hotkeys, chat channels,
        autostart and the on-screen overlay (Jarvis Bar / orb)? Only the default
        app does; a second listener on the same microphone / bot token / key
        combo — or a second bar on the same spot of the screen — is a collision,
        not redundancy. The overlay is the sharpest case: both instances read
        ONE ``jarvis.toml``, so a dev app that drew its own bar and then had it
        switched off wrote ``orb_style = "none"`` into the shared file and took
        the default app's bar away on its next restart (2026-08-23)."""
        return self.is_default

    # ---- naming ---------------------------------------------------------
    @property
    def label(self) -> str:
        """Short badge text: ``""`` for the default app, ``"Dev"`` for dev."""
        if self.is_default:
            return ""
        return DEV_LABEL if self.is_dev else self.name.title()

    @property
    def display_name(self) -> str:
        """Window title / taskbar name: ``"Personal Jarvis"`` or ``"… Dev"``."""
        return PRODUCT_NAME if self.is_default else f"{PRODUCT_NAME} {self.label}"

    @property
    def compact_name(self) -> str:
        return PRODUCT_COMPACT_NAME if self.is_default else f"{PRODUCT_COMPACT_NAME}{self.label}"

    @property
    def slug(self) -> str:
        return PRODUCT_SLUG if self.is_default else f"{PRODUCT_SLUG}-{self.name}"

    # ---- storage / network ----------------------------------------------
    @property
    def data_dir_name(self) -> str:
        """Runtime data directory name next to the checkout (``data`` / ``data-dev``)."""
        return "data" if self.is_default else f"data-{self.name}"

    @property
    def port_offset(self) -> int:
        return 0 if self.is_default else DEV_PORT_OFFSET

    def port(self, base: int) -> int:
        """The port this instance binds for a configured base port."""
        return int(base) + self.port_offset

    @property
    def state_file_suffix(self) -> str:
        """Suffix for per-user state files that must not be shared between the
        instances (``last_session.json`` → ``last_session.dev.json``)."""
        return "" if self.is_default else f".{self.name}"

    # ---- OS identity ----------------------------------------------------
    @property
    def icon_file_name(self) -> str:
        return DEFAULT_ICON_FILE_NAME if self.is_default else DEV_ICON_FILE_NAME

    @property
    def windows_aumid(self) -> str:
        if self.is_default:
            return WINDOWS_APP_USER_MODEL_ID
        return f"{WINDOWS_APP_USER_MODEL_ID}.{self.label}"

    @property
    def windows_mutex_name(self) -> str:
        return WINDOWS_MUTEX_NAME if self.is_default else f"{WINDOWS_MUTEX_NAME}_{self.name}"

    @property
    def windows_shortcut_file_name(self) -> str:
        return WINDOWS_SHORTCUT_FILE_NAME if self.is_default else f"{self.display_name}.lnk"

    @property
    def windows_branded_launcher_file_name(self) -> str:
        return (
            WINDOWS_BRANDED_LAUNCHER_FILE_NAME
            if self.is_default
            else f"{self.compact_name}.exe"
        )

    @property
    def linux_wm_class(self) -> str:
        return LINUX_WM_CLASS if self.is_default else self.slug

    @property
    def linux_desktop_entry_file_name(self) -> str:
        return LINUX_DESKTOP_ENTRY_FILE_NAME if self.is_default else f"{self.slug}.desktop"

    @property
    def macos_bundle_id(self) -> str:
        return MACOS_BUNDLE_ID if self.is_default else f"{MACOS_BUNDLE_ID}.{self.name}"

    # ---- launch ---------------------------------------------------------
    @property
    def launcher_args(self) -> tuple[str, ...]:
        """Extra launcher argv that selects this instance (empty for default)."""
        return () if self.is_default else ("--instance", self.name)

    def environ(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        """``base`` (default ``os.environ``) with this instance pinned in it."""
        env = dict(os.environ if base is None else base)
        if self.is_default:
            env.pop(INSTANCE_ENV_VAR, None)
        else:
            env[INSTANCE_ENV_VAR] = self.name
        return env


def resolve_instance(environ: Mapping[str, str] | None = None) -> InstanceIdentity:
    """The instance named by ``environ`` (default: the process environment)."""
    source = os.environ if environ is None else environ
    return InstanceIdentity(normalize_instance_name(source.get(INSTANCE_ENV_VAR)))


def current_instance() -> InstanceIdentity:
    """The instance THIS process runs as. Cheap; reads the env on every call so a
    test that sets the variable sees the change without any cache to clear."""
    return resolve_instance()


def select_instance(name: str | None) -> InstanceIdentity:
    """Pin ``name`` into the process environment and return its identity.

    The launcher calls this for ``--instance`` before importing anything that
    reads :data:`INSTANCE_ENV_VAR` at import time (``jarvis.core.config``,
    ``jarvis.ui.icon_utils``). Child processes — the relauncher, the branded
    re-exec, the unelevated copy — inherit the variable and come back as the
    same instance.
    """
    identity = InstanceIdentity(normalize_instance_name(name))
    if identity.is_default:
        os.environ.pop(INSTANCE_ENV_VAR, None)
    else:
        os.environ[INSTANCE_ENV_VAR] = identity.name
    return identity


def instance_data_dir(project_root: Path, identity: InstanceIdentity | None = None) -> Path:
    """``<project_root>/data`` or ``<project_root>/data-<name>``."""
    return project_root / (identity or current_instance()).data_dir_name


__all__ = [
    "DEFAULT_ICON_FILE_NAME",
    "DEFAULT_INSTANCE_NAME",
    "DEV_ICON_FILE_NAME",
    "DEV_INSTANCE_NAME",
    "DEV_LABEL",
    "DEV_PORT_OFFSET",
    "INSTANCE_ENV_VAR",
    "KNOWN_INSTANCE_NAMES",
    "InstanceIdentity",
    "InstanceNameError",
    "current_instance",
    "instance_data_dir",
    "normalize_instance_name",
    "resolve_instance",
    "select_instance",
]
