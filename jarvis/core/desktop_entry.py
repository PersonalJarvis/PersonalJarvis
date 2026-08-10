"""freedesktop Desktop Entry encoding — the ONE place `.desktop` text is built.

Linux is the only consumer, but the module is pure stdlib and imports nothing
from ``jarvis``, so it is CI-provable on any OS (the whole point of the
"write into a temp HOME" test style used by the autostart port).

Why this exists as a shared module: a ``.desktop`` file carries **two nested
escaping layers**, and every hand-rolled writer in this code base only ever
implemented a fragment of the outer one.

    1. *Key-file layer* (spec § "Value types"): the file is INI-shaped and the
       reader decodes ``\\s`` ``\\n`` ``\\t`` ``\\r`` ``\\\\`` inside every value.
    2. *Exec layer* (spec § "Exec variables" + § "Quoting"): the ``Exec=`` value
       is additionally parsed as an argument vector with double-quote quoting,
       and a literal percent sign must be written ``%%`` because ``%`` starts a
       field code.

The layers compose, which is why the specification itself notes that a literal
backslash inside a quoted argument needs **four** backslashes in the file: the
Exec layer doubles it, then the key-file layer doubles that again. Getting this
wrong fails silently on BOTH sides — the desktop environment discards an entry
with an unknown field code without a word, and a writer that compares the same
unescaped string it wrote still reports the dead entry as installed and current.

Reading goes through :func:`read_field`, which is group-aware on purpose:
``Exec=`` is a legal key in a ``[Desktop Action …]`` group too, so a naive
line-prefix scan can answer a drift check with a completely different command
once a desktop tool has rewritten the file.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

# Characters that force an Exec argument to be quoted (spec § Quoting). Note
# that ``%`` is deliberately absent: it needs doubling, not quoting.
_EXEC_RESERVED: frozenset[str] = frozenset(" \t\n\r\"'\\><~|&;$*?#()`")

# Characters that must additionally carry a backslash INSIDE a quoted argument.
_EXEC_QUOTE_ESCAPED: frozenset[str] = frozenset('"`$\\')

#: The group every launcher/autostart key we care about lives in.
DESKTOP_ENTRY_GROUP = "Desktop Entry"


def escape_value(value: str) -> str:
    """Encode ``value`` for the key-file layer (spec § Value types).

    Backslash first — the other replacements introduce backslashes that must
    not be escaped a second time. A directory name containing a backslash is
    perfectly legal on Linux, and unescaped it turns the rest of the value into
    a bogus escape sequence.
    """
    out = value.replace("\\", "\\\\")
    out = out.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return out


def unescape_value(value: str) -> str:
    """Decode a key-file value written by :func:`escape_value`.

    Used for drift detection: comparing the *decoded* on-disk value against the
    plain string we intended keeps the comparison honest even for an entry a
    desktop tool re-encoded differently than we would have. An undefined escape
    sequence is passed through verbatim rather than dropped, so a foreign file
    can never make the comparison silently succeed.
    """
    out: list[str] = []
    index = 0
    length = len(value)
    simple = {"n": "\n", "r": "\r", "t": "\t", "s": " ", "\\": "\\"}
    while index < length:
        char = value[index]
        if char != "\\" or index + 1 >= length:
            out.append(char)
            index += 1
            continue
        nxt = value[index + 1]
        if nxt in simple:
            out.append(simple[nxt])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def exec_argument(value: str) -> str:
    """Encode ONE ``Exec=`` argument through the Exec layer only.

    Quoting is decided on the raw value, escaping is applied to the
    percent-doubled one — a ``%`` never forces quotes, and the ``%%`` it becomes
    must not pick up a backslash.
    """
    token = value.replace("%", "%%")
    if any(char in _EXEC_RESERVED for char in value):
        body = "".join(f"\\{char}" if char in _EXEC_QUOTE_ESCAPED else char for char in token)
        return f'"{body}"'
    return token


def exec_value(program: str, args: Iterable[str] = ()) -> str:
    """Render a complete, spec-correct ``Exec=`` value.

    Both layers, in the order the reader unwinds them: Exec-quote each argument,
    then key-file-escape the joined command line. An empty program is returned
    as an empty string rather than a stray pair of quotes, so a caller with an
    unresolved interpreter writes an obviously-broken entry instead of a
    plausible-looking one.
    """
    if not program:
        return ""
    tokens: Sequence[str] = [exec_argument(program), *(exec_argument(a) for a in args)]
    return escape_value(" ".join(tokens))


def read_field(text: str, key: str, *, group: str = DESKTOP_ENTRY_GROUP) -> str | None:
    """The raw (still key-file-escaped) value of ``key`` in ``group``, or ``None``.

    Group-aware, comment-aware and tolerant of the spec-legal ``Key = value``
    spacing. Locale-suffixed keys (``Name[de]``) do NOT match their base key.
    A duplicated key resolves to the LAST occurrence, matching GLib's
    ``GKeyFile`` — the reader every mainstream desktop actually uses.
    """
    target = f"[{group}]"
    in_group = False
    found: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_group = line == target
            continue
        if not in_group or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            found = value.strip()
    return found


def reads_as_false(value: str | None) -> bool:
    """Is ``value`` an explicit boolean ``false`` in a ``.desktop`` file?

    ``None`` (key absent) is NOT false — the desktop-side "switched off"
    markers (``Hidden``, ``X-GNOME-Autostart-enabled``) default to enabled when
    missing, so only a written-out ``false`` may be read as a disabled entry.
    """
    return value is not None and value.strip().lower() == "false"


def reads_as_true(value: str | None) -> bool:
    """Is ``value`` an explicit boolean ``true`` in a ``.desktop`` file?"""
    return value is not None and value.strip().lower() == "true"


__all__ = [
    "DESKTOP_ENTRY_GROUP",
    "escape_value",
    "exec_argument",
    "exec_value",
    "read_field",
    "reads_as_false",
    "reads_as_true",
    "unescape_value",
]
