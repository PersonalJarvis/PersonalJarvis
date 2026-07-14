"""MCP capability verbs must cover every supported language (de/en/es).

Live bug 2026-07-14 (realtime voice turn 09:05): the German request
"Kannst du mir bitte mal gucken, alle all meine Notebooks auflisten?"
force-spawned a heavy Jarvis-Agent mission instead of the router/tool model
calling the notebooklm MCP list tool inline.  Root cause chain:

  1. ``_verbs_from_description`` extracted ONLY English verbs from the
     English MCP tool description, so ``resolve_intent`` could never match
     a German (or Spanish) phrasing of the same action.
  2. A generic German verb from an unrelated CLI capability ("gucken",
     gcloud seed catalog) made ``has_action_intent`` True.
  3. "action intent + no capability resolves" is exactly the
     ``_is_generic_subagent_work`` predicate, so the deterministic
     force-spawn dispatched a mission 44 ms after the transcript — before
     the LLM router ever saw the turn.

The fix expands each detected English verb with its known German/Spanish
speech-input forms at registration time, so a German phrasing resolves to
the MCP capability and the turn stays inline (the router calls the MCP
tool directly), matching the English behaviour.
"""
from __future__ import annotations

from jarvis.core.capabilities import Capability, CapabilityRegistry
from jarvis.mcp.adapter import _objects_from_tool_name, _verbs_from_description

# The exact final transcript of the live turn (trace c82aa1a6, 09:05:24).
_LIVE_GERMAN_TRANSCRIPT = (
    "Kannst du mir bitte mal gucken, "  # i18n-allow: live transcript under test
    "alle all meine Notebooks auflisten?"  # i18n-allow: live transcript under test
)

# Representative English MCP tool description (notebooklm-mcp notebook_list).
_LIST_DESCRIPTION = "List all notebooks in the user's NotebookLM account."


def _notebook_list_cap() -> Capability:
    """Build the capability exactly as MCPToolAdapter registers it."""
    return Capability(
        id="mcp.notebooklm-mcp/notebook_list",
        source="mcp",
        verbs=_verbs_from_description(_LIST_DESCRIPTION),
        objects=_objects_from_tool_name("notebooklm-mcp/notebook_list"),
        description=_LIST_DESCRIPTION,
        risk_tier="monitor",
        requires_evidence=True,
    )


def _gcloud_cli_cap() -> Capability:
    """Unrelated CLI capability with the generic verbs that made
    ``has_action_intent`` fire on the live turn (seed catalog entry)."""
    return Capability(
        id="cli.gcloud",
        source="cli",
        verbs=("zeig", "list", "check", "guck", "gucke", "gucken"),  # i18n-allow: input vocabulary
        objects=("gcp", "google cloud", "gcloud", "projekt"),  # i18n-allow: input vocabulary
        description="Google Cloud CLI.",
        risk_tier="safe",
        requires_evidence=False,
    )


class TestVerbsFromDescriptionMultilingual:
    def test_english_verb_still_extracted(self) -> None:
        verbs = _verbs_from_description(_LIST_DESCRIPTION)
        assert "list" in verbs

    def test_german_forms_included(self) -> None:
        verbs = _verbs_from_description(_LIST_DESCRIPTION)
        assert "auflisten" in verbs  # i18n-allow: speech-input vocabulary under test
        assert "liste" in verbs  # i18n-allow: speech-input vocabulary under test

    def test_spanish_forms_included(self) -> None:
        verbs = _verbs_from_description(_LIST_DESCRIPTION)
        assert "listar" in verbs

    def test_fallback_stays_use(self) -> None:
        assert _verbs_from_description("Frobnicate the widget.") == ("use",)

    def test_no_duplicates(self) -> None:
        verbs = _verbs_from_description("List, list and list again.")
        assert len(verbs) == len(set(verbs))


class TestGermanUtteranceResolvesToMcpCapability:
    """Regression guard for the live 2026-07-14 force-spawn."""

    def _registry(self) -> CapabilityRegistry:
        reg = CapabilityRegistry()
        reg.register(_gcloud_cli_cap())
        reg.register(_notebook_list_cap())
        return reg

    def test_live_transcript_resolves_to_mcp(self) -> None:
        reg = self._registry()
        cap = reg.resolve_intent(_LIVE_GERMAN_TRANSCRIPT)
        assert cap is not None
        assert cap.id == "mcp.notebooklm-mcp/notebook_list"

    def test_generic_subagent_predicate_no_longer_fires(self) -> None:
        """``has_action_intent AND resolve is None`` was the spawn trigger."""
        reg = self._registry()
        assert reg.has_action_intent(_LIVE_GERMAN_TRANSCRIPT)
        assert reg.resolve_intent(_LIVE_GERMAN_TRANSCRIPT) is not None

    def test_plain_german_list_phrasing_resolves(self) -> None:
        cap = self._registry().resolve_intent(
            "Liste bitte alle meine Notebooks auf."  # i18n-allow: speech-input phrasing under test
        )
        assert cap is not None
        assert cap.source == "mcp"

    def test_german_show_phrasing_resolves(self) -> None:
        cap = self._registry().resolve_intent(
            "Zeig mir alle meine Notebooks"  # i18n-allow: speech-input phrasing under test
        )
        assert cap is not None
        assert cap.source == "mcp"

    def test_english_resolution_unchanged(self) -> None:
        cap = self._registry().resolve_intent("Can you list all my notebooks?")
        assert cap is not None
        assert cap.id == "mcp.notebooklm-mcp/notebook_list"
