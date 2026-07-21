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
from collections.abc import Callable
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
_POINTER_RESOLVER: Any = None


def _get_ui_tree_source() -> Any:
    global _UI_TREE_SOURCE
    if _UI_TREE_SOURCE is None:
        from jarvis.vision.tree_factory import make_ui_tree_source  # noqa: PLC0415

        _UI_TREE_SOURCE = make_ui_tree_source()
    return _UI_TREE_SOURCE


def _get_pointer_resolver() -> Any:
    """Cached per-OS accessibility point resolver (hit-test at a point)."""
    global _POINTER_RESOLVER
    if _POINTER_RESOLVER is None:
        from jarvis.vision.element_at_point import (  # noqa: PLC0415
            make_pointer_resolver,
        )

        _POINTER_RESOLVER = make_pointer_resolver()
    return _POINTER_RESOLVER


def _query_focused_element() -> Any:
    """Seam over the native focused-element probe (tests monkeypatch this)."""
    from jarvis.vision.element_at_point import (  # noqa: PLC0415
        query_focused_element,
    )

    return query_focused_element()


async def _focused_element_async() -> Any:
    """The natively-resolved focused control, or ``None`` (bounded, safe)."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_query_focused_element), timeout=UIA_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001 — focus probe is best-effort evidence
        return None


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
    ``False`` — a FOCUSED editable field is readable and no editable holds
                the text (confirmed miss: we positively looked at the surface
                that would have received the input).
    ``None``  — nothing editable readable, text too short, or editables are
                readable but NONE holds focus — then the enumeration likely
                covered the WRONG surface (a start-menu/UWP flyout outside
                the foreground tree, live incident 2026-07-02 18:00) and a
                confident ``False`` would stall the mission on a lie.
    """
    t = normalize_for_value_match(typed)
    if len(t) < TYPE_VERIFY_MIN_CHARS:
        return None
    focused_editable_seen = False
    for node in nodes or ():
        if getattr(node, "role", "") not in EDITABLE_UIA_ROLES:
            continue
        if getattr(node, "focused", False):
            focused_editable_seen = True
        val = normalize_for_value_match(getattr(node, "value", "") or "")
        if t in val:
            return True
    return False if focused_editable_seen else None


#: Roles that are CONTAINERS or large regions, not controls — focus reported
#: on one of these (a browser window, a web view, a list, a document body)
#: is NOT evidence that a click hit its intended target: accepting it would
#: rescue genuine in-window misses and neuter the effect-check. Covers all
#: three role vocabularies (UIA / AX / AT-SPI), since the point resolver
#: returns platform-native role names. (Extended per the 2026-07-02
#: adversarial review: lists/tables/toolbars and the AT-SPI document-canvas
#: family were missing.)
_FOCUS_CONTAINER_ROLES = frozenset({
    # UIA (also the mapped vocabulary of the walked tree nodes)
    "Window", "Pane", "Document", "Group", "Custom", "TitleBar",
    "List", "Tree", "DataGrid", "Table", "ToolBar", "MenuBar",
    # macOS AX
    "AXWindow", "AXSheet", "AXGroup", "AXScrollArea", "AXWebArea",
    "AXApplication", "AXDrawer", "AXList", "AXTable", "AXOutline",
    "AXToolbar", "AXTabGroup", "AXSplitGroup", "AXLayoutArea",
    # AT-SPI role names
    "frame", "window", "panel", "application", "document frame",
    "document web", "filler", "list", "table", "tree", "tree table",
    "tool bar", "menu bar", "scroll pane", "viewport", "page tab list",
    "document text", "document spreadsheet", "document presentation",
    "document email",
})

#: A focused element LARGER than this fraction of the capture area is a
#: region, not a control — its focus is not click-target evidence (same
#: container-trap reasoning as the snap cap in
#: :data:`_SNAP_MAX_AREA_FRACTION`). Applied wherever bounds are known.
_FOCUS_MAX_AREA_FRACTION = 0.15


def click_point_in_focused_element(
    nodes: tuple, x: int, y: int, *, capture_area: int | None = None,
) -> bool | None:
    """Did the click point land inside the CONTROL holding keyboard focus?

    The rescue evidence for the "click produced no visible change" false
    miss: clicking a control that is ALREADY in the desired state (e.g. an
    address bar that is focused by default on a new tab) changes zero
    pixels, so the pixel effect-check alone fails the click, truncates the
    batched type behind it, and stalls the mission right at its goal (live
    incident 2026-07-02 19:06, Chrome guest new-tab). Focus + containment
    from the accessibility tree proves the click hit its target even though
    nothing visibly changed. Works on all three platforms (UIA / AX /
    AT-SPI bounds are screen input units).

    Two container traps never count as evidence (a focused WINDOW/region
    containing the point says nothing about the click's target, and would
    also skip the zoom-refine retry a real miss deserves):
    * role in :data:`_FOCUS_CONTAINER_ROLES`;
    * element area above :data:`_FOCUS_MAX_AREA_FRACTION` of
      ``capture_area`` (when the caller provides one).

    ``True``  — a focused control-sized element's bounds contain the point.
    ``False`` — nodes are readable, no qualifying element contains the point.
    ``None``  — no nodes readable (cannot tell).
    """
    if not nodes:
        return None
    max_area = (
        max(1, int(capture_area * _FOCUS_MAX_AREA_FRACTION))
        if capture_area else None
    )
    for node in nodes:
        if not getattr(node, "focused", False):
            continue
        if str(getattr(node, "role", "") or "") in _FOCUS_CONTAINER_ROLES:
            continue
        bounds = getattr(node, "bounds", None)
        if not bounds:
            continue
        try:
            bx, by, bw, bh = (int(v) for v in bounds)
        except (TypeError, ValueError):
            continue
        if max_area is not None and bw * bh > max_area:
            continue
        if bw > 0 and bh > 0 and bx <= x < bx + bw and by <= y < by + bh:
            return True
    return False


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


def clickable_rects(
    nodes: tuple, max_n: int = 200,
) -> list[tuple[str, str, tuple[int, int, int, int]]]:
    """Enabled clickable elements WITH their bounding rects, as
    ``(name, role, (x, y, w, h))`` in screen input units.

    The rects come from the accessibility tree the snapshot already walks
    (UIA physical pixels / AX points / AT-SPI pixels — the same units the
    actuator clicks in, now that the process is per-monitor DPI aware).
    Unlike :func:`clickable_labels` this keeps NAMELESS elements too: an
    icon-only button has no label but its rect still anchors a click.
    """
    out: list[tuple[str, str, tuple[int, int, int, int]]] = []
    for node in nodes or ():
        if not getattr(node, "enabled", True):
            continue
        role = getattr(node, "role", "")
        if role not in CLICKABLE_UIA_ROLES:
            continue
        bounds = getattr(node, "bounds", None) or (0, 0, 0, 0)
        try:
            x, y, w, h = (int(v) for v in bounds)
        except (TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue
        out.append(((getattr(node, "name", "") or "").strip(), role, (x, y, w, h)))
        if len(out) >= max_n:
            break
    return out


#: An element larger than this fraction of the capture area is a CONTAINER
#: (panel, document body) — snapping to its center would be a wrong-but-
#: plausible click far from the model's intent.
_SNAP_MAX_AREA_FRACTION = 0.15


def snap_point_to_element(
    x: int,
    y: int,
    clickables: list[tuple[str, str, tuple[int, int, int, int]]],
    *,
    capture_area: int,
    capture_rect: tuple[int, int, int, int] | None = None,
) -> tuple[int, int, str] | None:
    """Snap a model-predicted point to the CENTER of the smallest clickable
    element whose rect contains it — or ``None`` (keep the raw point).

    Converts the vision model's job from "hit an exact pixel" to "point
    anywhere inside the right element": the residual 1-3% pointing error
    stops mattering, at zero added latency (the rects ride the snapshot that
    already runs in parallel with the capture). The SMALLEST containing rect
    wins (leaf over ancestor); rects above ``_SNAP_MAX_AREA_FRACTION`` of
    the capture area never snap (container trap). When ``capture_rect``
    (left, top, width, height) is given, an element whose center lies
    OUTSIDE it never snaps — a window-scoped capture must not let the
    accessibility tree push a click out of the window the model saw.
    Pure; never raises.
    """
    best: tuple[int, str, int, int] | None = None  # (area, label, cx, cy)
    max_area = max(1, int(capture_area * _SNAP_MAX_AREA_FRACTION))
    for name, _role, (rx, ry, rw, rh) in clickables or ():
        if not (rx <= x < rx + rw and ry <= y < ry + rh):
            continue
        area = rw * rh
        if area > max_area:
            continue
        cx, cy = rx + rw // 2, ry + rh // 2
        if capture_rect is not None:
            cl, ct, cw, ch = capture_rect
            if not (cl <= cx < cl + cw and ct <= cy < ct + ch):
                continue
        if best is None or area < best[0]:
            best = (area, name, cx, cy)
    if best is None:
        return None
    return (best[2], best[3], best[1])


# ---------------------------------------------------------------------------
# Async tree snapshots (best-effort; failures degrade to "cannot tell")
# ---------------------------------------------------------------------------

async def foreground_ui_snapshot(
    timeout_s: float = UIA_TIMEOUT_S,
    max_labels: int = 28,
    observation_guard: Callable[[], bool] | None = None,
) -> tuple[list[str], str, str | None, list[tuple[str, str, tuple[int, int, int, int]]]]:
    """One tree observation → (clickable labels, field-values hint, handoff
    reason, clickable rects). ``([], "", None, [])`` on any failure or a
    label-less surface — that empty path self-gates the loop back to raw
    pixel clicks (canvas apps expose no useful tree)."""
    if observation_guard is not None and not observation_guard():
        raise RuntimeError("foreground window changed before UI-tree observation")
    try:
        obs = await asyncio.wait_for(
            _get_ui_tree_source().observe(), timeout=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort enumeration
        logger.debug("[cu] UI-tree snapshot failed (non-fatal): %s", exc)
        return [], "", None, []
    if observation_guard is not None and not observation_guard():
        raise RuntimeError("foreground window changed during UI-tree observation")
    nodes = tuple(getattr(obs, "nodes", ()) or ())
    return (
        clickable_labels(nodes, max_n=max_labels),
        field_values_hint(nodes),
        human_handoff_reason(nodes),
        clickable_rects(nodes),
    )


async def verify_typed_text(typed: str) -> bool | None:
    """Did the typed text land in an editable field? Tri-state, positive-first.

    Two evidence paths:

    1. The walked foreground tree (:func:`typed_text_landed`) — but that walk
       is depth- and node-capped, and Chrome nests its omnibox BELOW the walk
       depth, so on browsers the field that actually received the text is
       often invisible to it (which turned real typed URLs into "did NOT
       land" false failures — Windows, 2026-07-21).
    2. The NATIVE focused-element probe (UIA ``GetFocusedElement`` / AX
       ``AXFocusedUIElement``) — depth-free; its value is read directly off
       the control that holds keyboard focus.

    The native probe is consulted whenever the walk did not positively
    confirm, and can only UPGRADE the verdict to ``True`` — it never
    manufactures a ``False`` (an omnibox may render its value as the
    highlighted autocomplete suggestion, so a non-matching read is not
    proof of a miss).
    """
    walked: bool | None
    try:
        obs = await asyncio.wait_for(
            _get_ui_tree_source().observe(), timeout=UIA_TIMEOUT_S,
        )
        walked = typed_text_landed(
            tuple(getattr(obs, "nodes", ()) or ()), typed,
        )
    except Exception:  # noqa: BLE001
        walked = None
    if walked is True:
        return True
    target = normalize_for_value_match(typed)
    if len(target) < TYPE_VERIFY_MIN_CHARS:
        return walked
    element = await _focused_element_async()
    if element is not None:
        value = normalize_for_value_match(
            str(getattr(element, "value", "") or ""),
        )
        if target in value:
            return True
    return walked


async def verify_click_focus(name: str) -> bool | None:
    """Fresh tree → :func:`element_is_focused`. Any error → ``None``."""
    try:
        obs = await asyncio.wait_for(
            _get_ui_tree_source().observe(), timeout=UIA_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001
        return None
    return element_is_focused(tuple(getattr(obs, "nodes", ()) or ()), name)


async def verify_click_focus_point(
    x: int, y: int, *, capture_area: int | None = None,
) -> bool | None:
    """Is the element at ``(x, y)`` the focused control? Error → ``None``.

    Three evidence paths, positive-only, all cross-platform:

    1. Native point hit-test (UIA ``ElementFromPoint`` / AX element-at-
       position / AT-SPI ``getAccessibleAtPoint``) — depth- and pruning-
       independent, so it works even where the walked tree cannot reach the
       control (Chrome nests its omnibox below the walk depth).
    2. Native focused-element probe (UIA ``GetFocusedElement`` / AX
       ``AXFocusedUIElement``): when the hit-test lands on a CONTAINER
       (Chromium sometimes reports a Pane/Group at the omnibox point), the
       focused control's own bounds containing the click point is equally
       strong already-in-desired-state evidence (the BUG-038 rescue
       regressed exactly here — Windows, 2026-07-21).
    3. Fallback: scan the walked foreground tree
       (:func:`click_point_in_focused_element`).

    Container focus never counts (:data:`_FOCUS_CONTAINER_ROLES`), and a
    focused element larger than :data:`_FOCUS_MAX_AREA_FRACTION` of
    ``capture_area`` is a region, not a target (applied where bounds are
    known — the macOS/Linux hit-test reports no bounds yet and relies on
    the role deny-list). Runs ONLY on the click-miss path (no happy-path
    latency): the engine consults it before declaring a zero-pixel-change
    click a miss.
    """
    try:
        element = await asyncio.wait_for(
            asyncio.to_thread(_get_pointer_resolver().at, int(x), int(y)),
            timeout=UIA_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001 — hit-test is best-effort
        element = None
    if (
        element is not None
        and getattr(element, "focused", None) is True
        and str(getattr(element, "role", "") or "")
        not in _FOCUS_CONTAINER_ROLES
    ):
        bounds = getattr(element, "bounds", None) or (0, 0, 0, 0)
        area = max(0, int(bounds[2])) * max(0, int(bounds[3]))
        if (
            not capture_area
            or area == 0  # backend reports no bounds: role filter decides
            or area <= max(1, int(capture_area * _FOCUS_MAX_AREA_FRACTION))
        ):
            return True
    focused = await _focused_element_async()
    if (
        focused is not None
        and getattr(focused, "focused", None) is True
        and str(getattr(focused, "role", "") or "")
        not in _FOCUS_CONTAINER_ROLES
    ):
        fb = getattr(focused, "bounds", None) or (0, 0, 0, 0)
        f_area = max(0, int(fb[2])) * max(0, int(fb[3]))
        if (
            f_area > 0
            and fb[0] <= int(x) < fb[0] + fb[2]
            and fb[1] <= int(y) < fb[1] + fb[3]
            and (
                not capture_area
                or f_area
                <= max(1, int(capture_area * _FOCUS_MAX_AREA_FRACTION))
            )
        ):
            return True
    try:
        obs = await asyncio.wait_for(
            _get_ui_tree_source().observe(), timeout=UIA_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001
        return None
    return click_point_in_focused_element(
        tuple(getattr(obs, "nodes", ()) or ()), int(x), int(y),
        capture_area=capture_area,
    )


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


def crop_raw(
    raw: tuple[tuple[int, int], bytes],
    rect: tuple[int, int, int, int],
    cx: int,
    cy: int,
    radius: int,
) -> tuple[tuple[int, int], bytes] | None:
    """Crop a square around screen point ``(cx, cy)`` out of a raw grab
    covering screen ``rect`` (left, top, width, height in input units).
    ``None`` when the point lies outside the grab. The grab's pixel size may
    differ from the rect (Retina: point rect, pixel grab) — the screen point
    resolves into grab pixels through the ONE central translation
    (:class:`jarvis.cu.geometry.CoordinateMapper`), never hand-rolled math.
    Pure PIL, no re-grab — one grab yields both the local and the global
    effect signal."""
    from jarvis.cu.geometry import CoordinateMapper  # noqa: PLC0415

    (w, h), rgb = raw
    rl, rt, rw, rh = rect
    if rw <= 0 or rh <= 0:
        return None
    try:
        mapper = CoordinateMapper(
            capture_left=rl, capture_top=rt,
            capture_width=rw, capture_height=rh,
            image_width=w, image_height=h,
        )
    except ValueError:  # aspect mismatch: this grab is not of this rect
        return None
    if not mapper.contains_screen(int(cx), int(cy)):
        return None
    px, py = mapper.screen_to_image(int(cx), int(cy))
    from PIL import Image  # noqa: PLC0415

    # Radius in input units -> grab pixels (uniform capture scale).
    r = max(1, int(radius * w / rw))
    left = max(0, px - r)
    top = max(0, py - r)
    right = min(w, px + r)
    bottom = min(h, py + r)
    img = Image.frombytes("RGB", (w, h), rgb).crop((left, top, right, bottom))
    return ((img.width, img.height), img.tobytes())


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
