"""Pre/post verification for Computer-Use v2 actions.

Ports the legacy engine's PROVEN deterministic read-back helpers (type
read-back, focus confirmation, field-value hints, human-handoff detection —
all pure accessibility-tree inspections, no LLM calls) and adds the v2
effect-check primitives:

* :func:`regions_equal` — raw pre/post pixel comparison around an action
  point ("did anything near the click visibly react?").
* :func:`screen_drifted` — has the screen visibly changed since the frame
  the model decided on? If yes, acting on stale coordinates is a guess —
  the loop re-perceives instead (pre-execution UI-state verification).

CRITICAL sourcing rule (documented trap): UIA nodes/values come ONLY from
the dedicated tree-source enumeration below — never from a screenshot-mode
``observation.nodes``, which is always empty in the CU path.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

UIA_TIMEOUT_S = 3.0
TYPE_VERIFY_MIN_CHARS = 3

EDITABLE_UIA_ROLES = frozenset({"Edit", "Document", "ComboBox"})
CLICKABLE_UIA_ROLES = frozenset({
    "Button", "MenuItem", "ListItem", "TabItem", "CheckBox", "RadioButton",
    "Hyperlink", "Edit", "ComboBox", "TreeItem", "SplitButton", "Text",
})

# Human-handoff cues: screens the USER must handle — the agent must never
# type a secret it does not hold (AP-2).
_CAPTCHA_CUES = (
    "captcha", "recaptcha", "hcaptcha", "not a robot", "verify you are human",
    "verify you're human", "are you human", "human verification",
)
_TWOFACTOR_CUES = (
    "two-factor", "two factor", "2fa", "one-time code", "one time code",
    "verification code", "security code", "authenticator", "otp",
)
_PASSWORD_FIELD_TOKENS = ("password", "passwort", "passcode")  # noqa: S105 — field-label tokens, not a credential; "passwort" is German UI matching data (product surface bucket 3)

# Cached UI tree source (constructing one per step pays setup cost for the
# same foreground enumeration).
_UI_TREE_SOURCE: Any = None


def _get_ui_tree_source() -> Any:
    global _UI_TREE_SOURCE
    if _UI_TREE_SOURCE is None:
        from jarvis.vision.tree_factory import make_ui_tree_source  # noqa: PLC0415

        _UI_TREE_SOURCE = make_ui_tree_source()
    return _UI_TREE_SOURCE


# ---------------------------------------------------------------------------
# Pure node inspections (ported from the legacy engine, behavior-identical)
# ---------------------------------------------------------------------------

def normalize_for_value_match(text: str) -> str:
    """Lowercase, trim, strip URL scheme + ``www.`` so ``https://example.com``
    matches a goal phrased ``go to example.com``."""
    s = (text or "").strip().casefold()
    for scheme in ("https://", "http://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
            break
    if s.startswith("www."):
        s = s[len("www."):]
    return s.strip("/").strip()


def typed_text_landed(nodes: tuple, typed: str) -> bool | None:
    """Tri-state type read-back (conservative):

    ``True``  — an editable field's value contains the typed text.
    ``False`` — editable fields ARE readable and none holds it (confirmed miss).
    ``None``  — nothing editable readable / text too short (cannot tell).
    """
    t = normalize_for_value_match(typed)
    if len(t) < TYPE_VERIFY_MIN_CHARS:
        return None
    editable_seen = False
    for node in nodes or ():
        if getattr(node, "role", "") not in EDITABLE_UIA_ROLES:
            continue
        editable_seen = True
        val = normalize_for_value_match(getattr(node, "value", "") or "")
        if t in val:
            return True
    return False if editable_seen else None


def element_is_focused(nodes: tuple, name: str) -> bool | None:
    """Positive-only focus confirmation after ``click_element`` (``True`` or
    ``None`` — a button legitimately may not retain focus, so absence never
    flips a click to a false failure)."""
    target = (name or "").strip().lower()
    if len(target) < 2:
        return None
    for node in nodes or ():
        if not getattr(node, "focused", False):
            continue
        nm = (getattr(node, "name", "") or "").strip().lower()
        if nm and (nm == target or target in nm or nm in target):
            return True
    return None


def field_values_hint(nodes: tuple) -> str:
    """Model-facing hint listing editable controls that already hold text
    (drives correct ``clear_first`` decisions). Values ride only in the model
    message — never a log line or TTS (AP-2)."""
    lines: list[str] = []
    for node in nodes or ():
        if getattr(node, "role", "") not in EDITABLE_UIA_ROLES:
            continue
        val = (getattr(node, "value", "") or "").strip()
        if not val:
            continue
        nm = (getattr(node, "name", "") or "").strip()
        lines.append(f'"{nm or "field"}" currently contains "{val[:120]}"')
        if len(lines) >= 8:
            break
    if not lines:
        return ""
    return (
        "\n\nFIELD CONTENTS (a field already holding text — to REPLACE it, "
        "set clear_first on the type action):\n" + "\n".join(lines)
    )


def human_handoff_reason(nodes: tuple) -> str | None:
    """Detect a screen the human must handle (CAPTCHA > 2FA > password entry).

    Inspects only node names/roles + the secure-edit flag, never a field
    VALUE (AP-2). Conservative: a bare "password" word is not enough — a
    real password EDIT field or an explicit CAPTCHA/2FA phrase is required.
    """
    captcha = twofactor = has_password_field = False
    for node in nodes or ():
        if getattr(node, "is_password", False):
            has_password_field = True
        nm = (getattr(node, "name", "") or "").strip().lower()
        if nm:
            if any(c in nm for c in _CAPTCHA_CUES):
                captcha = True
            if any(c in nm for c in _TWOFACTOR_CUES):
                twofactor = True
            if getattr(node, "role", "") in EDITABLE_UIA_ROLES and any(
                t in nm for t in _PASSWORD_FIELD_TOKENS
            ):
                has_password_field = True
    if captcha:
        return "captcha challenge"
    if twofactor:
        return "two-factor / one-time code"
    if has_password_field:
        return "login / password entry"
    return None


def clickable_labels(nodes: tuple, max_n: int = 28) -> list[str]:
    """Foreground clickable control names for the CLICKABLE ELEMENTS hint."""
    names: list[str] = []
    for node in nodes or ():
        if not getattr(node, "enabled", True):
            continue
        if getattr(node, "role", "") not in CLICKABLE_UIA_ROLES:
            continue
        nm = (getattr(node, "name", "") or "").strip()
        if nm and len(nm) <= 40 and nm not in names:
            names.append(nm)
        if len(names) >= max_n:
            break
    return names


# ---------------------------------------------------------------------------
# Async tree snapshots (best-effort; failures degrade to "cannot tell")
# ---------------------------------------------------------------------------

async def foreground_ui_snapshot(
    timeout_s: float = UIA_TIMEOUT_S, max_labels: int = 28,
) -> tuple[list[str], str, str | None]:
    """One tree observation → (clickable labels, field-values hint, handoff
    reason). ``([], "", None)`` on any failure or a label-less surface —
    that empty path self-gates the loop back to pixel clicks."""
    try:
        obs = await asyncio.wait_for(
            _get_ui_tree_source().observe(), timeout=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort enumeration
        logger.debug("[cu] UI-tree snapshot failed (non-fatal): %s", exc)
        return [], "", None
    nodes = tuple(getattr(obs, "nodes", ()) or ())
    return (
        clickable_labels(nodes, max_n=max_labels),
        field_values_hint(nodes),
        human_handoff_reason(nodes),
    )


async def verify_typed_text(typed: str) -> bool | None:
    """Fresh tree → :func:`typed_text_landed`. Any error → ``None``."""
    try:
        obs = await asyncio.wait_for(
            _get_ui_tree_source().observe(), timeout=UIA_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001
        return None
    return typed_text_landed(tuple(getattr(obs, "nodes", ()) or ()), typed)


async def verify_click_focus(name: str) -> bool | None:
    """Fresh tree → :func:`element_is_focused`. Any error → ``None``."""
    try:
        obs = await asyncio.wait_for(
            _get_ui_tree_source().observe(), timeout=UIA_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001
        return None
    return element_is_focused(tuple(getattr(obs, "nodes", ()) or ()), name)


# ---------------------------------------------------------------------------
# Pixel-effect primitives
# ---------------------------------------------------------------------------

def regions_equal(
    a: tuple[tuple[int, int], bytes] | None,
    b: tuple[tuple[int, int], bytes] | None,
) -> bool | None:
    """Raw pre/post region comparison. ``None`` when either grab failed
    (cannot tell) — never turns a missing grab into a false miss."""
    if a is None or b is None:
        return None
    return a[0] == b[0] and a[1] == b[1]


def screen_drifted(
    reference: tuple[tuple[int, int], bytes] | None,
    current: tuple[tuple[int, int], bytes] | None,
) -> bool:
    """Has the screen visibly changed between two raw grabs? Used as the
    pre-execution check: acting on coordinates from a frame the screen has
    since departed from is a guess. Unknown (failed grab) counts as NOT
    drifted — refusing to act on an unreadable-but-fine screen would stall
    missions on hosts where region grabs are flaky."""
    if reference is None or current is None:
        return False
    from jarvis.cu.capture import frames_differ  # noqa: PLC0415

    return frames_differ(reference, current)
