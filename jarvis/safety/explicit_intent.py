"""Conservative, target-bound authorization for a single shell deletion.

Free-form mentions, negation, inferred names, relative paths and compound shell
programs cannot waive confirmation. Only an imperative naming the exact absolute
operand can do so. Ambiguous requests use the ordinary approval workflow.
"""

from __future__ import annotations

import re
import sys
from pathlib import PurePosixPath, PureWindowsPath

# No evaluation, globbing, redirection, options, provider paths or quote escapes.
_PATH = r"(?:[A-Za-z]:[\\/]|/)[\w ./\\-]+"
_OPERAND = (
    rf"(?:'(?P<single>{_PATH})'|\"(?P<double>{_PATH})\"|(?P<bare>(?:[A-Za-z]:[\\/]|/)[\w./\\-]+))"
)
_REQUEST = re.compile(
    r"(?:(?:please|bitte|por favor)\s+)?"
    r"(?:delete|remove|erase|lösch|lösche|entferne|borra|elimina)\s+"
    r"(?:(?:the file|the folder|the directory|die Datei|den Ordner|el archivo|la carpeta)\s+)?"
    + _OPERAND,
    re.IGNORECASE,
)
_COMMAND = re.compile(
    r"(?:rm\s+(?:(?:-r|--recursive)\s+)?(?:--\s+)?|Remove-Item\s+(?:-LiteralPath\s+)?)"
    + _OPERAND
    + r"(?:\s+-Recurse)?",
    re.IGNORECASE,
)


def command_confirms_destruction(
    command: str, utterance: str, *, windows: bool | None = None
) -> bool:
    """Match one deletion and its exact, literal absolute target, or fail closed."""
    if any(char.isspace() and char not in " \t" for char in command):
        return False
    request = _REQUEST.fullmatch(utterance.strip())
    invocation = _COMMAND.fullmatch(command.strip())
    if request is None or invocation is None:
        return False
    requested = next(value for value in request.groups() if value is not None)
    operand = next(value for value in invocation.groups() if value is not None)
    # Do not normalize paths: shell/platform semantics can differ. Exact spelling
    # is required, and an entire filesystem is never implicit consent.
    if operand != requested or ".." in operand.replace("\\", "/").split("/"):
        return False
    if windows is None:
        windows = sys.platform == "win32"
    if not windows and "\\" in operand and invocation.group("single") is None:
        # POSIX unquoted/double-quoted backslashes can change the actual operand.
        return False
    path = PureWindowsPath(operand) if windows else PurePosixPath(operand)
    return path.is_absolute() and str(path) != path.anchor
