"""whoami tool: tells the user what Jarvis knows about them.

Called by the brain when the user asks "What do you know about me?",
"Tell me what you've learned about me", "What do you know about me?" etc.

Output is a **naturally spoken** summary (3-4 sentences) — no Markdown,
no bullet points, no emojis. Goes straight into TTS and is read aloud
to the user.

Risk tier: safe — pure read-only access to USER.md + people/*.md.
"""
from __future__ import annotations

from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.memory.people import PersonStore
from jarvis.memory.user_profile import UserProfile


class WhoAmITool:
    name: str = "whoami"
    risk_tier: str = "safe"
    description: str = (
        "Returns a summary of what Jarvis knows about the user."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "detail_level": {
                "type": "string",
                "enum": ["short", "full"],
                "description": (
                    "'short' (default) = 3-4 sentences to read aloud, "
                    "'full' = more detailed, with all known fields."
                ),
                "default": "short",
            },
        },
        "required": [],
    }

    def __init__(
        self,
        *,
        profile: UserProfile | None = None,
        people: PersonStore | None = None,
    ) -> None:
        """Dependencies injected via constructor — `ExecutionContext` has no
        profile access.

        Args:
            profile: The UserProfile handle (USER.md). Can be `None` if the
                workspace could not be loaded — in that case `execute`
                returns a friendly error message instead of crashing.
            people: Optional `PersonStore` for the "Laura as partner,
                Paul as colleague" line.
        """
        self._profile = profile
        self._people = people

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        if self._profile is None:
            # Workspace could not be loaded — gentle spoken fallback (below).
            return ToolResult(
                success=True,
                output=(
                    "Ich habe gerade keinen Zugriff auf Dein Profil — "  # i18n-allow
                    "das Workspace ist noch nicht eingerichtet. Sag Bescheid "  # i18n-allow
                    "wenn Du den Bootstrap starten willst."  # i18n-allow
                ),
            )

        detail_level = (args.get("detail_level") or "short").strip().lower()
        if detail_level not in ("short", "full"):
            detail_level = "short"

        try:
            meta = self._profile.meta
            text = self._render_summary(meta, detail_level)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                success=False,
                output=None,
                error=f"{type(exc).__name__}: {exc}",
            )

        return ToolResult(success=True, output=text)

    # ------------------------------------------------------------------
    # Rendering — bewusst als reine Logik, damit unit-testbar
    # ------------------------------------------------------------------

    def _render_summary(self, meta: dict[str, Any], detail_level: str) -> str:
        """Builds the spoken summary from the profile fields.

        Strategy: we collect the strongest signals (name, communication style,
        values, pet peeves) into natural sentences. Never "According to your
        profile...", but directly "You're called Alex and you like direct
        answers."
        """
        identity = meta.get("identity", {}) or {}
        communication = meta.get("communication", {}) or {}
        values = meta.get("values", {}) or {}
        work_style = meta.get("work_style", {}) or {}
        relationship = meta.get("relationship", {}) or {}

        name = identity.get("name")
        address = identity.get("preferred_address") or name

        # --- Empty-profile check: only name (or not even that) present ---
        has_signal = any([
            communication.get("directness") is not None,
            communication.get("formality") is not None,
            communication.get("verbosity"),
            communication.get("humor_types"),
            values.get("top_values"),
            values.get("pet_peeves"),
            work_style.get("focus_mode"),
            work_style.get("planning_horizon"),
            relationship.get("feedback_pref"),
        ])
        has_people = bool(self._people and self._people.list_all())

        if not has_signal and not has_people:
            if name:
                return (
                    f"Noch nicht viel, ausser Deinem Namen — Du heisst {name}. "  # i18n-allow
                    f"Willst Du kurz durch den Bootstrap, damit ich Dich besser kennenlerne?"  # i18n-allow
                )
            return (
                "Noch gar nichts — ich kenne nicht mal Deinen Namen. "  # i18n-allow
                "Willst Du kurz durch den Bootstrap?"  # i18n-allow
            )

        # --- Sentence 1: name + form of address ---
        sentences: list[str] = []
        if name:
            if address and address != name:
                sentences.append(f"Du heisst {name} und ich spreche Dich als {address} an.")  # i18n-allow
            else:
                sentences.append(f"Du heisst {name}.")  # i18n-allow

        # --- Sentence 2: communication style, in natural language ---
        comm_phrase = self._communication_phrase(communication)
        if comm_phrase:
            sentences.append(comm_phrase)

        # --- Sentence 3: values + pet peeves ---
        values_phrase = self._values_phrase(values)
        if values_phrase:
            sentences.append(values_phrase)

        # --- Sentence 4: work style / feedback style (only for full, or if there's still room) ---
        if detail_level == "full":
            ws_phrase = self._work_style_phrase(work_style, relationship)
            if ws_phrase:
                sentences.append(ws_phrase)
        elif len(sentences) < 3:
            # In short mode: at most 1 extra sentence if little has been said so far
            ws_phrase = self._work_style_phrase(work_style, relationship)
            if ws_phrase:
                sentences.append(ws_phrase)

        # --- People line ---
        people_phrase = self._people_phrase()
        if people_phrase:
            sentences.append(people_phrase)

        # Short mode: cap at max 4 sentences (the people line may be additional).
        if detail_level == "short" and len(sentences) > 4:
            sentences = sentences[:4]

        return " ".join(sentences)

    # ------------------------------------------------------------------
    # Sentence building blocks
    # ------------------------------------------------------------------

    def _communication_phrase(self, comm: dict[str, Any]) -> str:
        """Builds a natural sentence about the communication style."""
        traits: list[str] = []
        directness = comm.get("directness")
        if isinstance(directness, (int, float)):
            if directness >= 4:
                traits.append("magst direkte, klare Antworten")  # i18n-allow
            elif directness <= 2:
                traits.append("bevorzugst eher diplomatische Formulierungen")  # i18n-allow

        formality = comm.get("formality")
        if isinstance(formality, (int, float)):
            if formality <= 2:
                traits.append("stehst auf lockeren Ton")  # i18n-allow
            elif formality >= 4:
                traits.append("magst es eher foermlich")  # i18n-allow

        verbosity = comm.get("verbosity")
        if verbosity == "concise":
            traits.append("willst knappe Antworten")  # i18n-allow
        elif verbosity == "detailed":
            traits.append("magst ausfuehrliche Erklaerungen")  # i18n-allow

        humor = comm.get("humor_types") or []
        if humor:
            humor_str = " und ".join(humor) if len(humor) <= 2 else ", ".join(humor)
            traits.append(f"schaetzt {humor_str} Humor")  # i18n-allow

        if comm.get("emoji_ok") is False:
            traits.append("willst keine Emojis")  # i18n-allow

        if not traits:
            return ""

        if len(traits) == 1:
            return f"Du {traits[0]}."  # i18n-allow
        if len(traits) == 2:
            return f"Du {traits[0]} und {traits[1]}."  # i18n-allow
        # 3+: comma-separated, last one joined with "und" (German "and")
        body = ", ".join(traits[:-1]) + f" und {traits[-1]}"
        return f"Du {body}."  # i18n-allow

    def _values_phrase(self, values: dict[str, Any]) -> str:
        """Builds a sentence about values + pet peeves."""
        top = values.get("top_values") or []
        peeves = values.get("pet_peeves") or []

        parts: list[str] = []
        if top:
            if len(top) == 1:
                parts.append(f"Wichtig ist Dir {top[0]}")  # i18n-allow
            else:
                items = ", ".join(top[:-1]) + f" und {top[-1]}"
                parts.append(f"Wichtig sind Dir {items}")  # i18n-allow

        if peeves:
            if len(peeves) == 1:
                parts.append(f"gar nicht ab kannst Du {peeves[0]}")  # i18n-allow
            else:
                items = ", ".join(peeves[:-1]) + f" und {peeves[-1]}"
                parts.append(f"gar nicht ab kannst Du {items}")  # i18n-allow

        if not parts:
            return ""
        return "; ".join(parts) + "."

    def _work_style_phrase(
        self, work_style: dict[str, Any], relationship: dict[str, Any]
    ) -> str:
        """Sentence about work style / feedback style."""
        bits: list[str] = []
        focus = work_style.get("focus_mode")
        if focus:
            bits.append(f"arbeitest im {focus}-Modus")  # i18n-allow
        horizon = work_style.get("planning_horizon")
        if horizon:
            bits.append(f"denkst in {horizon}")  # i18n-allow
        fb = relationship.get("feedback_pref")
        if fb:
            bits.append(f"willst Feedback {fb}")  # i18n-allow

        if not bits:
            return ""
        if len(bits) == 1:
            return f"Du {bits[0]}."  # i18n-allow
        return f"Du {', '.join(bits[:-1])} und {bits[-1]}."  # i18n-allow

    def _people_phrase(self) -> str:
        """List of known people in the user's circle — 1 sentence."""
        if not self._people:
            return ""
        try:
            people = self._people.list_all()
        except Exception:  # noqa: BLE001
            return ""
        if not people:
            return ""

        # Name up to 3 people — more becomes unwieldy in a spoken sentence.
        snippets: list[str] = []
        for p in people[:3]:
            rel = (p.relationship or "").strip()
            if rel and rel.lower() != "unbekannt":  # i18n-allow
                snippets.append(f"{p.name} als {rel}")  # i18n-allow
            else:
                snippets.append(p.name)

        if not snippets:
            return ""
        if len(snippets) == 1:
            return f"Du hast {snippets[0]} erwaehnt."  # i18n-allow
        body = ", ".join(snippets[:-1]) + f" und {snippets[-1]}"
        return f"Du hast {body} erwaehnt."  # i18n-allow
