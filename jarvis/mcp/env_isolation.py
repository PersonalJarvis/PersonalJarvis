"""The environment an untrusted MCP server is allowed to see.

A community plugin is code from a stranger that the user pressed Install on.
It runs as a child process of Jarvis, and a child process inherits its
parent's environment — which on a normal developer machine is where the
OPENAI_API_KEY, the ANTHROPIC_API_KEY, the GITHUB_TOKEN and every other
credential the owner ever exported happen to live. Reading them costs a
plugin one line, and nothing about that line looks like an attack.

So an untrusted server does not get the environment. It gets the smallest
one a process can still start and reach the network in, plus the values its
own manifest declared (``env_template``, which is checked to hold nothing but
``$plugin_…`` placeholders resolved from that plugin's own token).

The list is an ALLOWLIST for the same reason the launcher rules are: naming
the secrets to remove means being wrong the day someone invents a new one.

Not covered, and worth knowing: the delegated worker path
(`marketplace.mcp_bridge`) hands a server definition to the claude CLI, and
that process starts the server itself with an environment this code never
sees. Isolation there would have to come from the CLI.
"""

from __future__ import annotations

import os
import sys

__all__ = ["isolated_environment"]

#: Everything a process needs to start, find its libraries, write a temp file
#: and resolve a host. Nothing here identifies the user to a remote service.
_POSIX_KEEP = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TERM",
    "TZ",
    "SHELL",
    "USER",
    "LOGNAME",
)

#: The Windows equivalent. SYSTEMROOT and friends are not optional — without
#: them the loader cannot even find the C runtime, and the process dies before
#: its first line with an error that names nothing.
_WINDOWS_KEEP = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "COMMONPROGRAMFILES",
    "HOMEDRIVE",
    "HOMEPATH",
    "USERPROFILE",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "OS",
)

#: Reaching the outside world at all. `npx`/`uvx` download a package on first
#: run, so a machine behind a corporate proxy has plugins that simply never
#: start without these.
#:
#: Honest trade-off: a proxy URL can carry `user:pass@`, so this hands a
#: plugin one credential in exchange for working at all on those machines. It
#: is a far smaller loss than the API keys the whole environment would leak,
#: and a plugin that talks to the network sees the proxy either way.
_NETWORK_KEEP = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
)

#: `docker run` is one of the three allowed launchers and cannot find the
#: daemon without this on a non-default setup (Colima, a remote engine, a
#: rootless socket). It names a socket, not a secret.
_LAUNCHER_KEEP = ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")


def _keep_names() -> tuple[str, ...]:
    base = _WINDOWS_KEEP if sys.platform == "win32" else _POSIX_KEEP
    return base + _NETWORK_KEEP + _LAUNCHER_KEEP


def isolated_environment(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for an untrusted stdio server.

    ``overrides`` are the plugin's own declared variables and are applied
    last, so a plugin can still be handed its own token — and only its own.
    """
    keep = _keep_names()
    # Windows environment names are case-insensitive; matching on the upper
    # form keeps `Path` and `PATH` the same variable, which they are.
    if sys.platform == "win32":
        wanted = {name.upper() for name in keep}
        env = {key: value for key, value in os.environ.items() if key.upper() in wanted}
    else:
        env = {key: os.environ[key] for key in keep if key in os.environ}
    if overrides:
        env.update({str(k): str(v) for k, v in overrides.items()})
    return env
