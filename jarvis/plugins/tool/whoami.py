"""whoami-Tool: erzaehlt dem User, was Jarvis ueber ihn weiss.

Wird vom Brain aufgerufen wenn der User fragt "Was weisst du ueber mich?",
"Erzaehl mir was du dir gemerkt hast", "What do you know about me?" etc.

Output ist eine **natuerlich gesprochene** Zusammenfassung (3-4 Saetze) —
kein Markdown, keine Aufzaehlungszeichen, keine Emojis. Landet direkt in
der TTS und wird dem User vorgelesen.

Risk-Tier: safe — reiner Read-Only-Zugriff auf USER.md + people/*.md.
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
        "Liefert eine Zusammenfassung was Jarvis ueber den User weiss."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "detail_level": {
                "type": "string",
                "enum": ["short", "full"],
                "description": (
                    "'short' (default) = 3-4 Saetze zum Vorlesen, "
                    "'full' = ausfuehrlicher mit allen bekannten Feldern."
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
        """Dependencies per Constructor — `ExecutionContext` hat keinen Profile-Zugriff.

        Args:
            profile: Das UserProfile-Handle (USER.md). Kann `None` sein, wenn
                der Workspace nicht geladen werden konnte — dann liefert
                `execute` eine freundliche Fehlermeldung statt zu crashen.
            people: Optionaler `PersonStore` fuer die "Laura als Partnerin,
                Paul als Kollegen"-Zeile.
        """
        self._profile = profile
        self._people = people

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        if self._profile is None:
            # Workspace konnte nicht geladen werden — sanfte Fehlermeldung.
            return ToolResult(
                success=True,
                output=(
                    "Ich habe gerade keinen Zugriff auf Dein Profil — "
                    "das Workspace ist noch nicht eingerichtet. Sag Bescheid "
                    "wenn Du den Bootstrap starten willst."
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
        """Baut die gesprochene Zusammenfassung aus den Profile-Feldern.

        Strategie: wir sammeln die staerksten Signale (Name, Kommunikationsstil,
        Werte, Pet Peeves) in natuerlichen Saetzen. Kein "Laut Profil...",
        sondern direkt "Du heisst Alex und magst direkte Antworten."
        """
        identity = meta.get("identity", {}) or {}
        communication = meta.get("communication", {}) or {}
        values = meta.get("values", {}) or {}
        work_style = meta.get("work_style", {}) or {}
        relationship = meta.get("relationship", {}) or {}

        name = identity.get("name")
        address = identity.get("preferred_address") or name

        # --- Empty-Profile-Check: nur Name (oder nicht mal das) vorhanden ---
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
                    f"Noch nicht viel, ausser Deinem Namen — Du heisst {name}. "
                    f"Willst Du kurz durch den Bootstrap, damit ich Dich besser kennenlerne?"
                )
            return (
                "Noch gar nichts — ich kenne nicht mal Deinen Namen. "
                "Willst Du kurz durch den Bootstrap?"
            )

        # --- Satz 1: Name + Anrede ---
        sentences: list[str] = []
        if name:
            if address and address != name:
                sentences.append(f"Du heisst {name} und ich spreche Dich als {address} an.")
            else:
                sentences.append(f"Du heisst {name}.")

        # --- Satz 2: Kommunikationsstil in natuerlicher Sprache ---
        comm_phrase = self._communication_phrase(communication)
        if comm_phrase:
            sentences.append(comm_phrase)

        # --- Satz 3: Werte + Pet Peeves ---
        values_phrase = self._values_phrase(values)
        if values_phrase:
            sentences.append(values_phrase)

        # --- Satz 4: Arbeitsweise / Feedback-Stil (nur bei full oder falls noch Platz) ---
        if detail_level == "full":
            ws_phrase = self._work_style_phrase(work_style, relationship)
            if ws_phrase:
                sentences.append(ws_phrase)
        elif len(sentences) < 3:
            # Bei short-Mode: max 1 zusaetzlicher Satz wenn noch wenig gesagt
            ws_phrase = self._work_style_phrase(work_style, relationship)
            if ws_phrase:
                sentences.append(ws_phrase)

        # --- Personen-Zeile ---
        people_phrase = self._people_phrase()
        if people_phrase:
            sentences.append(people_phrase)

        # Short-Mode: auf max 4 Saetze cappen (Personen-Zeile darf zusaetzlich sein).
        if detail_level == "short" and len(sentences) > 4:
            sentences = sentences[:4]

        return " ".join(sentences)

    # ------------------------------------------------------------------
    # Satzbausteine
    # ------------------------------------------------------------------

    def _communication_phrase(self, comm: dict[str, Any]) -> str:
        """Baut einen natuerlichen Satz zum Kommunikationsstil."""
        traits: list[str] = []
        directness = comm.get("directness")
        if isinstance(directness, (int, float)):
            if directness >= 4:
                traits.append("magst direkte, klare Antworten")
            elif directness <= 2:
                traits.append("bevorzugst eher diplomatische Formulierungen")

        formality = comm.get("formality")
        if isinstance(formality, (int, float)):
            if formality <= 2:
                traits.append("stehst auf lockeren Ton")
            elif formality >= 4:
                traits.append("magst es eher foermlich")

        verbosity = comm.get("verbosity")
        if verbosity == "concise":
            traits.append("willst knappe Antworten")
        elif verbosity == "detailed":
            traits.append("magst ausfuehrliche Erklaerungen")

        humor = comm.get("humor_types") or []
        if humor:
            humor_str = " und ".join(humor) if len(humor) <= 2 else ", ".join(humor)
            traits.append(f"schaetzt {humor_str} Humor")

        if comm.get("emoji_ok") is False:
            traits.append("willst keine Emojis")

        if not traits:
            return ""

        if len(traits) == 1:
            return f"Du {traits[0]}."
        if len(traits) == 2:
            return f"Du {traits[0]} und {traits[1]}."
        # 3+: mit Kommas, letztes mit "und"
        body = ", ".join(traits[:-1]) + f" und {traits[-1]}"
        return f"Du {body}."

    def _values_phrase(self, values: dict[str, Any]) -> str:
        """Baut einen Satz mit Werten + Pet Peeves."""
        top = values.get("top_values") or []
        peeves = values.get("pet_peeves") or []

        parts: list[str] = []
        if top:
            if len(top) == 1:
                parts.append(f"Wichtig ist Dir {top[0]}")
            else:
                items = ", ".join(top[:-1]) + f" und {top[-1]}"
                parts.append(f"Wichtig sind Dir {items}")

        if peeves:
            if len(peeves) == 1:
                parts.append(f"gar nicht ab kannst Du {peeves[0]}")
            else:
                items = ", ".join(peeves[:-1]) + f" und {peeves[-1]}"
                parts.append(f"gar nicht ab kannst Du {items}")

        if not parts:
            return ""
        return "; ".join(parts) + "."

    def _work_style_phrase(
        self, work_style: dict[str, Any], relationship: dict[str, Any]
    ) -> str:
        """Satz zu Arbeitsweise/Feedback-Stil."""
        bits: list[str] = []
        focus = work_style.get("focus_mode")
        if focus:
            bits.append(f"arbeitest im {focus}-Modus")
        horizon = work_style.get("planning_horizon")
        if horizon:
            bits.append(f"denkst in {horizon}")
        fb = relationship.get("feedback_pref")
        if fb:
            bits.append(f"willst Feedback {fb}")

        if not bits:
            return ""
        if len(bits) == 1:
            return f"Du {bits[0]}."
        return f"Du {', '.join(bits[:-1])} und {bits[-1]}."

    def _people_phrase(self) -> str:
        """Liste der bekannten Personen im Umfeld — 1 Satz."""
        if not self._people:
            return ""
        try:
            people = self._people.list_all()
        except Exception:  # noqa: BLE001
            return ""
        if not people:
            return ""

        # Bis zu 3 Personen nennen — mehr wird im gesprochenen Satz unhandlich.
        snippets: list[str] = []
        for p in people[:3]:
            rel = (p.relationship or "").strip()
            if rel and rel.lower() != "unbekannt":
                snippets.append(f"{p.name} als {rel}")
            else:
                snippets.append(p.name)

        if not snippets:
            return ""
        if len(snippets) == 1:
            return f"Du hast {snippets[0]} erwaehnt."
        body = ", ".join(snippets[:-1]) + f" und {snippets[-1]}"
        return f"Du hast {body} erwaehnt."
