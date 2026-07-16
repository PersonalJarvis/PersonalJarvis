"""Import-clean helpers for decoding macOS Accessibility geometry.

Accessibility position and size attributes are ``AXValueRef`` objects on a
real Mac.  PyObjC 8.4 and newer ship manual wrappers for ``AXValueCreate`` and
``AXValueGetValue``; the generic metadata bridge used by older releases could
crash when handed the C output pointer.  Keep that ABI-sensitive operation in
one small module, use only the manual wrapper, and degrade to ``None`` when an
old or incomplete PyObjC installation is encountered.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any


def _finite_pair(first: Any, second: Any) -> tuple[float, float] | None:
    try:
        pair = float(first), float(second)
    except (TypeError, ValueError):
        return None
    return pair if all(math.isfinite(item) for item in pair) else None


def _manual_axvalue_pair(
    value: Any,
    constant_names: tuple[str, ...],
) -> tuple[float, float] | None:
    """Decode with PyObjC's crash-safe manual wrapper, never raw ``ctypes``."""
    try:
        import HIServices  # type: ignore[import-not-found] # noqa: PLC0415

        value_type = next(
            getattr(HIServices, name)
            for name in constant_names
            if hasattr(HIServices, name)
        )
        get_type = getattr(HIServices, "AXValueGetType", None)
        if callable(get_type) and get_type(value) != value_type:
            return None
        ok, decoded = HIServices.AXValueGetValue(value, value_type, None)
        if not ok:
            return None
        # PyObjC generated structs expose sequence slots without registering
        # with collections.abc.Sequence. Capability access works for both
        # those structs and the tuple returned by newer bridge releases.
        try:
            if len(decoded) != 2:
                return None
            return _finite_pair(decoded[0], decoded[1])
        except (AttributeError, IndexError, KeyError, TypeError):
            return None
    except Exception:  # noqa: BLE001 -- unsupported PyObjC safely degrades
        return None


def decode_ax_point(value: Any) -> tuple[float, float] | None:
    """Decode a CGPoint-like value without importing PyObjC off macOS."""
    if value is None:
        return None
    if isinstance(value, dict):
        return _finite_pair(
            value.get("x", value.get("X")),
            value.get("y", value.get("Y")),
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) == 2:
            return _finite_pair(value[0], value[1])
    x, y = getattr(value, "x", None), getattr(value, "y", None)
    if x is not None and y is not None:
        return _finite_pair(x, y)
    return _manual_axvalue_pair(
        value,
        ("kAXValueCGPointType", "kAXValueTypeCGPoint"),
    )


def decode_ax_size(value: Any) -> tuple[float, float] | None:
    """Decode a CGSize-like value without importing PyObjC off macOS."""
    if value is None:
        return None
    if isinstance(value, dict):
        return _finite_pair(
            value.get("w", value.get("width", value.get("Width"))),
            value.get("h", value.get("height", value.get("Height"))),
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) == 2:
            return _finite_pair(value[0], value[1])
    width = getattr(value, "width", None)
    height = getattr(value, "height", None)
    if width is not None and height is not None:
        return _finite_pair(width, height)
    return _manual_axvalue_pair(
        value,
        ("kAXValueCGSizeType", "kAXValueTypeCGSize"),
    )
