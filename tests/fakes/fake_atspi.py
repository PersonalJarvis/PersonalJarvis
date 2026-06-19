"""Hand-built Linux AT-SPI fake (Wave 2.2, EK-3).

Per CLAUDE.md the project uses real fakes, never ``unittest.mock``. ``pyatspi``
is GObject-Introspection, distro-packaged (not on PyPI) and cannot be installed
on the Windows dev box; the real AT-SPI capture only runs on a Linux leg with a
live accessibility bus (``skip_ci``). This fake gives the flatten +
role-normalization + bus-degrade logic a deterministic, OS-free AT-SPI tree.

``FakeAtspiAccessible`` is duck-typed to the seam ``jarvis.vision.atspi_tree``
reads: ``role_token`` (the ``ROLE_*`` token the role map keys on), ``name``,
``childCount`` / ``getChildAtIndex``, ``getState().contains(...)``, and
``extents`` (an (x, y, w, h) tuple ``_atspi_bounds`` reads directly). A test
wires ``AtspiTreeSource(traverser=...)`` to walk a ``FakeAtspiAccessible`` root
and ``bus_check=...`` to flip the AD-13 bus gate.
"""

# ``role_token`` is an AT-SPI accessibility role name (e.g. "ROLE_PUSH_BUTTON"),
# not a credential — S106's "hardcoded password" heuristic false-positives on
# the "*_token" arg name across the canned-tree builders.
# ruff: noqa: S106

from __future__ import annotations

from typing import Any


class FakeAtspiStateSet:
    """A canned AT-SPI state set; ``contains`` checks membership of a token set."""

    def __init__(self, states: set[str] | None = None) -> None:
        self._states = states or set()

    def contains(self, state: Any) -> bool:
        return str(state) in self._states


class _FakeAtspiText:
    """A canned AT-SPI Text interface; ``getText`` returns the field's content."""

    def __init__(self, text: str) -> None:
        self._text = text

    def getText(self, start: int, end: int) -> str:  # noqa: N802 — AT-SPI surface
        return self._text


class FakeAtspiAccessible:
    """A canned AT-SPI ``Accessible`` node."""

    def __init__(
        self,
        *,
        role_token: str,
        name: str = "",
        extents: tuple[int, int, int, int] = (0, 0, 0, 0),
        states: set[str] | None = None,
        pid: int = 4242,
        children: list[FakeAtspiAccessible] | None = None,
        text: str | None = None,
    ) -> None:
        self.role_token = role_token
        self.name = name
        self.extents = extents
        self._states = FakeAtspiStateSet(states)
        self._pid = pid
        self._children = children or []
        # Editable accessibles expose the Text interface; non-editable ones
        # (buttons, labels) do NOT, so the value-read must be optional. Only set
        # ``queryText`` when text was provided, mirroring the real AT-SPI surface.
        if text is not None:
            self.queryText = lambda: _FakeAtspiText(text)  # noqa: N803

    @property
    def childCount(self) -> int:  # noqa: N802 — mirrors the AT-SPI surface
        return len(self._children)

    def getChildAtIndex(self, index: int) -> FakeAtspiAccessible | None:  # noqa: N802
        if 0 <= index < len(self._children):
            return self._children[index]
        return None

    def getState(self) -> FakeAtspiStateSet:  # noqa: N802
        return self._states

    def get_process_id(self) -> int:
        return self._pid


class FakeAtspiDesktop:
    """A canned AT-SPI desktop root holding one or more application children."""

    def __init__(self, apps: list[FakeAtspiAccessible]) -> None:
        self._apps = apps

    @property
    def childCount(self) -> int:  # noqa: N802
        return len(self._apps)

    def getChildAtIndex(self, index: int) -> FakeAtspiAccessible | None:  # noqa: N802
        if 0 <= index < len(self._apps):
            return self._apps[index]
        return None


def build_fake_atspi_traverser(
    app: FakeAtspiAccessible,
    *,
    window_title: str = "fake-app",
    pid: int = 4242,
):
    """Return a ``traverser(depth, window_title_filter)`` for ``AtspiTreeSource``.

    Runs the module-level ``_atspi_flatten`` against the canned root, so the test
    exercises the *real* flatten + role normalization while feeding scripted data.
    """
    from jarvis.vision.atspi_tree import _atspi_flatten  # local import: code under test

    def _traverse(max_depth: int, _window_title_filter: str | None):
        nodes: list[Any] = []
        _atspi_flatten(app, depth=0, max_depth=max_depth, parent_index=-1, out=nodes)
        return (window_title, pid, nodes)

    return _traverse


def make_canned_atspi_tree() -> FakeAtspiAccessible:
    """A small representative AT-SPI tree: frame > {button, entry, link, label}.

    Native ``ROLE_*`` tokens span the role-map table so the test can assert each
    normalizes to its canonical UIA role.
    """
    return FakeAtspiAccessible(
        role_token="ROLE_FRAME",
        name="Demo Frame",
        extents=(0, 0, 800, 600),
        states={"STATE_ACTIVE", "STATE_ENABLED"},
        children=[
            FakeAtspiAccessible(
                role_token="ROLE_PUSH_BUTTON",
                name="Save",
                extents=(10, 10, 80, 30),
                states={"STATE_ENABLED", "STATE_SENSITIVE"},
            ),
            FakeAtspiAccessible(
                role_token="ROLE_ENTRY",
                name="search",
                extents=(10, 50, 200, 24),
                states={"STATE_ENABLED"},
            ),
            FakeAtspiAccessible(
                role_token="ROLE_LINK",
                name="Learn more",
                extents=(10, 90, 120, 20),
                states={"STATE_ENABLED"},
            ),
            FakeAtspiAccessible(
                role_token="ROLE_CHECK_BOX",
                name="Remember me",
                extents=(10, 120, 160, 20),
                states={"STATE_ENABLED"},
            ),
            # A structural container that the role map drops.
            FakeAtspiAccessible(
                role_token="ROLE_PANEL",
                extents=(10, 150, 300, 200),
                children=[
                    FakeAtspiAccessible(
                        role_token="ROLE_LABEL",
                        name="A label",
                        extents=(20, 160, 180, 18),
                        states={"STATE_ENABLED"},
                    ),
                ],
            ),
        ],
    )


def make_canned_atspi_desktop() -> FakeAtspiDesktop:
    """A desktop root whose single active application is the canned tree."""
    return FakeAtspiDesktop([make_canned_atspi_tree()])


__all__ = [
    "FakeAtspiStateSet",
    "FakeAtspiAccessible",
    "FakeAtspiDesktop",
    "build_fake_atspi_traverser",
    "make_canned_atspi_tree",
    "make_canned_atspi_desktop",
]
