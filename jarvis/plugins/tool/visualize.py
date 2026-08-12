"""``visualize`` — draw what was just discussed, when the user asks for it.

Router-tier, risk ``safe``: it writes one HTML file into the run archive the
app already owns and moves the UI to the section that shows it. Nothing leaves
the machine, nothing existing is modified. A direct safe-gated action, never a
spawn — it never enters a worker tool set (AP-5/AP-14).

**Ask-only.** The tool is withheld from the model's tool set entirely on any
turn that did not explicitly ask for a picture; see
:mod:`jarvis.brain.visualize_gate` and
``BrainManager._hide_visualize_tool_without_request``. The gate is the real
enforcement — this description is the second line, for the turns where the gate
opens but the request was actually about something else.

The model supplies the STRUCTURE, never the markup: a title, a shape, and a
handful of short labels. Python owns the drawing
(:mod:`jarvis.visuals.render`). That is what keeps a picture cheap — a dozen
labels instead of a page of hand-written HTML — and what makes it safe to serve
inside the app origin, since nothing model-authored is ever markup.
"""

from __future__ import annotations

import logging
from typing import Any

from jarvis.core.events import NavigateSidebar
from jarvis.core.protocols import ToolResult
from jarvis.visuals.render import render_visual_html
from jarvis.visuals.spec import (
    MAX_ITEMS,
    VISUAL_KINDS,
    VisualSpecError,
    parse_spec,
)
from jarvis.visuals.store import store_visual

log = logging.getLogger(__name__)

# What each shape is FOR, in the model's terms. The picture is only useful if
# the shape matches the thought, and "kind" is the one field a model gets wrong
# in a way no validation can catch — so each option says when to pick it.
_KIND_GUIDE = (
    "'flow' = ordered steps or a process; "
    "'hierarchy' = parts within parts (use 'children'); "
    "'comparison' = options side by side; "
    "'timeline' = moments in chronological order; "
    "'bars' = quantities compared (every item needs a numeric 'value')."
)


class VisualizeTool:
    """Render one requested picture into the run archive and show it."""

    name: str = "visualize"
    risk_tier: str = "safe"
    description: str = (
        "Draw the thing under discussion as a picture, and open it in the "
        "Visualization section. Use this ONLY when the user explicitly asked to "
        "SEE something — 'visualisier mir das', 'zeig mir das bildlich', 'mach "
        "ein Diagramm draus', 'draw me a flowchart', 'show me that visually'. "
        "Never call it to decorate an ordinary answer, and never on your own "
        "initiative: an unrequested picture is a bug. "
        "It does NOT open the existing gallery — that is 'navigate' with "
        "section 'visualization'. Supply the STRUCTURE only (a title and a few "
        "short labels); the page itself is rendered for you, so never write "
        "HTML. " + _KIND_GUIDE
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": (
                    "The picture's heading — what it shows, in a few words. "
                    "Written in the language the user is speaking."
                ),
            },
            "kind": {
                "type": "string",
                "enum": list(VISUAL_KINDS),
                "description": "Which shape fits the thought. " + _KIND_GUIDE,
            },
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_ITEMS,
                "description": (
                    f"The content, in order. At most {MAX_ITEMS} — pick the ones "
                    "that carry the point rather than everything you know."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "description": "Short name of this step/part/option.",
                        },
                        "detail": {
                            "type": "string",
                            "description": "One short line of explanation. Optional.",
                        },
                        "value": {
                            "type": "number",
                            "description": "The quantity. Required for kind 'bars'.",
                        },
                        "children": {
                            "type": "array",
                            "description": "Nested items. Only for kind 'hierarchy'.",
                            "items": {"type": "object"},
                        },
                    },
                    "required": ["label"],
                },
            },
            "caption": {
                "type": "string",
                "description": "One closing line under the picture. Optional.",
            },
        },
        "required": ["title", "kind", "items"],
    }

    def __init__(self, bus: Any) -> None:
        self._bus = bus

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        # A rejected spec is the model's to fix, so the error text is the
        # validator's message verbatim — vague ones cost a wasted retry.
        try:
            spec = parse_spec(args or {}, source_utterance=_utterance(ctx))
        except VisualSpecError as exc:
            # Reported through the ToolResult's own error field, not a log call.
            return ToolResult(success=False, output={"kind": args.get("kind")}, error=str(exc))

        try:
            stored = store_visual(
                render_visual_html(spec),
                title=spec.title,
                utterance=spec.source_utterance,
            )
        except OSError as exc:
            # An unwritable archive is the machine's problem, not the model's:
            # reported honestly rather than retried into the same wall.
            log.warning("visualize_store_failed error=%s", exc)
            return ToolResult(
                success=False,
                output={"title": spec.title},
                error=f"The picture could not be saved: {exc}",
            )

        # Publishing is best-effort on purpose: the picture EXISTS and is listed
        # in the gallery either way, so a bus fault must not turn a successful
        # render into a failed tool call the model then retries.
        try:
            await self._bus.publish(
                NavigateSidebar(section="visualization", source_layer="brain.tool.visualize")
            )
        except Exception as exc:  # noqa: BLE001 — see comment above
            log.warning("visualize_navigate_failed error=%s", exc)

        return ToolResult(
            success=True,
            output={
                "slug": stored.slug,
                "artifact_path": stored.artifact_path,
                "kind": spec.kind,
                # The model reads this back to the user, so it says what was
                # drawn and where it is — not that a file was written.
                "summary": (
                    f"Drew '{spec.title}' as a {spec.kind} with {len(spec.items)} "
                    "items and opened it in the Visualization section."
                ),
            },
        )


def _utterance(ctx: Any) -> str:
    """What the user said this turn, when the context carries it.

    Used for the run label in the archive. Defensive across context shapes: the
    label is decoration, and no picture should fail over a missing attribute.
    """
    for attribute in ("user_text", "utterance", "text"):
        value = getattr(ctx, attribute, None)
        if isinstance(value, str) and value.strip():
            return value
    if isinstance(ctx, dict):
        for key in ("user_text", "utterance", "text"):
            value = ctx.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


__all__ = ["VisualizeTool"]
