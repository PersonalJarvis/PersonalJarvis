"""Unit tests for `jarvis.memory.user_profile.UserProfile`.

Covers: get/set, append_list with dedupe, save+reload roundtrip,
render_for_prompt (name + budget), reload after manual file edits.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.memory.user_profile import MAX_PROMPT_CHARS, UserProfile
from jarvis.memory.workspace import Workspace


# ======================================================================
# Setter/Getter auf Frontmatter-Clusters
# ======================================================================

class TestSetAndGet:
    def test_set_new_value_returns_true(self, profile: UserProfile) -> None:
        assert profile.set("identity", "name", "Ruben") is True
        assert profile.get("identity", "name") == "Ruben"

    def test_set_same_value_returns_false(self, profile: UserProfile) -> None:
        profile.set("identity", "name", "Ruben")
        # Same value → no change
        assert profile.set("identity", "name", "Ruben") is False

    def test_set_overwrites_existing_value(self, profile: UserProfile) -> None:
        profile.set("identity", "name", "Ruben")
        assert profile.set("identity", "name", "Paul") is True
        assert profile.get("identity", "name") == "Paul"

    def test_set_rejects_unknown_cluster(self, profile: UserProfile) -> None:
        with pytest.raises(ValueError):
            profile.set("unknown_cluster", "field", "value")

    def test_get_rejects_unknown_cluster(self, profile: UserProfile) -> None:
        with pytest.raises(ValueError):
            profile.get("unknown_cluster", "field")

    def test_get_returns_none_for_unset_field(self, profile: UserProfile) -> None:
        # The template sets "name" to null — this is passed through as None
        assert profile.get("identity", "name") is None


# ======================================================================
# append_list mit Dedupe
# ======================================================================

class TestAppendList:
    def test_append_new_value(self, profile: UserProfile) -> None:
        assert profile.append_list("values", "pet_peeves", "confirmation-fatigue") is True
        peeves = profile.get("values", "pet_peeves")
        assert peeves == ["confirmation-fatigue"]

    def test_append_duplicate_returns_false(self, profile: UserProfile) -> None:
        profile.append_list("values", "pet_peeves", "buzzwords")
        # Zweiter Append mit identischem Value → False
        assert profile.append_list("values", "pet_peeves", "buzzwords") is False
        # Liste bleibt bei Laenge 1
        assert profile.get("values", "pet_peeves") == ["buzzwords"]

    def test_append_onto_existing_list(self, profile: UserProfile) -> None:
        profile.append_list("communication", "humor_types", "dry")
        assert profile.append_list("communication", "humor_types", "nerdy") is True
        assert profile.get("communication", "humor_types") == ["dry", "nerdy"]

    def test_append_rejects_unknown_cluster(self, profile: UserProfile) -> None:
        with pytest.raises(ValueError):
            profile.append_list("foo", "bar", "baz")


# ======================================================================
# clear / remove_list_item — manual deletion from the Profile UI
# ======================================================================

class TestClearField:
    def test_clear_scalar_returns_true_and_empties(self, profile: UserProfile) -> None:
        profile.set("identity", "name", "Ruben")
        assert profile.clear("identity", "name") is True
        # The field reads back as unset (None) — "not known yet".
        assert profile.get("identity", "name") is None

    def test_clear_already_empty_returns_false(self, profile: UserProfile) -> None:
        # Template ships name=null → nothing to clear → no change.
        assert profile.clear("identity", "name") is False

    def test_clear_list_field_empties_it(self, profile: UserProfile) -> None:
        profile.append_list("identity", "languages", "Deutsch")
        assert profile.clear("identity", "languages") is True
        # Empty list / None — either reads as "not known yet" to the UI.
        assert not profile.get("identity", "languages")

    def test_clear_rejects_unknown_cluster(self, profile: UserProfile) -> None:
        with pytest.raises(ValueError):
            profile.clear("unknown_cluster", "name")

    def test_clear_survives_save_reload(self, profile: UserProfile) -> None:
        profile.set("identity", "preferred_address", "Chef")
        profile.save()
        profile.clear("identity", "preferred_address")
        profile.save()
        reloaded = UserProfile.load(profile.path)
        assert reloaded.get("identity", "preferred_address") is None


class TestRemoveListItem:
    def test_remove_existing_item(self, profile: UserProfile) -> None:
        profile.append_list("identity", "languages", "Deutsch")
        profile.append_list("identity", "languages", "English")
        assert profile.remove_list_item("identity", "languages", "Deutsch") is True
        assert profile.get("identity", "languages") == ["English"]

    def test_remove_absent_item_returns_false(self, profile: UserProfile) -> None:
        profile.append_list("identity", "languages", "Deutsch")
        assert profile.remove_list_item("identity", "languages", "Spanish") is False
        assert profile.get("identity", "languages") == ["Deutsch"]

    def test_remove_last_item_leaves_field_empty(self, profile: UserProfile) -> None:
        profile.append_list("identity", "languages", "Deutsch")
        assert profile.remove_list_item("identity", "languages", "Deutsch") is True
        assert not profile.get("identity", "languages")

    def test_remove_from_unset_field_returns_false(self, profile: UserProfile) -> None:
        assert profile.remove_list_item("values", "top_values", "Pizza") is False

    def test_remove_rejects_unknown_cluster(self, profile: UserProfile) -> None:
        with pytest.raises(ValueError):
            profile.remove_list_item("foo", "bar", "baz")

    def test_remove_survives_save_reload(self, profile: UserProfile) -> None:
        profile.append_list("identity", "languages", "Deutsch")
        profile.append_list("identity", "languages", "English")
        profile.save()
        profile.remove_list_item("identity", "languages", "Deutsch")
        profile.save()
        reloaded = UserProfile.load(profile.path)
        assert reloaded.get("identity", "languages") == ["English"]


# ======================================================================
# save / load / reload Roundtrip
# ======================================================================

class TestPersistence:
    def test_save_and_reload_roundtrip(self, profile: UserProfile) -> None:
        profile.set("identity", "name", "Ruben")
        profile.set("identity", "preferred_address", "Chef")
        profile.append_list("values", "pet_peeves", "buzzwords")
        profile.append_list("communication", "humor_types", "dry")
        profile.save()

        # Frisch laden → alle Werte wieder da
        reloaded = UserProfile.load(profile.path)
        assert reloaded.get("identity", "name") == "Ruben"
        assert reloaded.get("identity", "preferred_address") == "Chef"
        assert reloaded.get("values", "pet_peeves") == ["buzzwords"]
        assert reloaded.get("communication", "humor_types") == ["dry"]

    def test_save_updates_last_updated_timestamp(self, profile: UserProfile) -> None:
        profile.set("identity", "name", "Ruben")
        profile.save()
        reloaded = UserProfile.load(profile.path)
        # last_updated is set automatically on every save
        assert reloaded.meta.get("last_updated") is not None

    def test_reload_picks_up_manual_file_edits(
        self, profile: UserProfile, tmp_path: Path
    ) -> None:
        """reload() must pick up manual edits to USER.md (respect user edits)."""
        # Zustand: leeres Profile.
        assert profile.get("identity", "name") is None

        # User editiert USER.md manuell — simulieren wir durch direkten File-Write.
        new_text = (
            "---\n"
            "schema_version: 1\n"
            "identity:\n"
            "  name: HandEdited\n"
            "  preferred_address: null\n"
            "---\n\n"
            "# Body"
        )
        profile.path.write_text(new_text, encoding="utf-8")

        # In-Memory-Instanz weiss davon noch nichts
        assert profile.get("identity", "name") is None

        # But it does after reload
        profile.reload()
        assert profile.get("identity", "name") == "HandEdited"

    def test_load_raises_for_missing_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.md"
        with pytest.raises(FileNotFoundError):
            UserProfile.load(missing)


# ======================================================================
# append_observation — schreibt in die Markdown-Section
# ======================================================================

class TestAppendObservation:
    def test_observation_lands_in_section(self, profile: UserProfile) -> None:
        profile.append_observation(
            field_label="communication.humor_types",
            value="dry",
            evidence="User: 'bitte nicht zu albern'",  # i18n-allow: simulated German user evidence quote, content under test
        )
        profile.save()
        text = profile.path.read_text(encoding="utf-8")
        # Zeile steht zwischen den Markern
        assert "communication.humor_types: dry" in text
        assert "<!-- curator:observations:start -->" in text
        assert "<!-- curator:observations:end -->" in text

    def test_observation_is_deduped(self, profile: UserProfile) -> None:
        profile.append_observation("x.y", "v", "evidence")
        profile.append_observation("x.y", "v", "evidence")
        profile.save()
        text = profile.path.read_text(encoding="utf-8")
        # Only one instance
        assert text.count("x.y: v") == 1


# ======================================================================
# render_for_prompt
# ======================================================================

class TestRenderForPrompt:
    def test_render_includes_name_when_set(self, profile: UserProfile) -> None:
        profile.set("identity", "name", "Ruben")
        out = profile.render_for_prompt()
        assert "Ruben" in out
        # Header is still included
        assert "Ueber den User" in out  # i18n-allow: matches the real (currently German) header rendered by UserProfile.render_for_prompt

    def test_render_stays_within_budget(self, profile: UserProfile) -> None:
        """Even with a lot of content, rendering must not exceed the budget cap."""
        profile.set("identity", "name", "Ruben")
        profile.set("communication", "verbosity", "deep-dive")
        profile.append_list("communication", "humor_types", "dry")
        profile.append_list("communication", "humor_types", "nerdy")
        profile.append_list("values", "top_values", "autonomie")
        profile.append_list("values", "pet_peeves", "buzzwords")
        # Viele lange Observations erzwingen Truncation
        for i in range(50):
            profile.append_observation(
                "communication.misc",
                f"value_{i}_" + "x" * 80,
                f"evidence for #{i}",
            )

        out = profile.render_for_prompt()
        assert len(out) <= MAX_PROMPT_CHARS

    def test_render_respects_custom_max_chars(self, profile: UserProfile) -> None:
        profile.set("identity", "name", "Ruben")
        short = profile.render_for_prompt(max_chars=80)
        assert len(short) <= 80

    def test_render_without_name_still_works(self, profile: UserProfile) -> None:
        """Template has name=null — rendering must not crash regardless."""
        out = profile.render_for_prompt()
        assert isinstance(out, str)
        assert "Ueber den User" in out  # i18n-allow: matches the real (currently German) header rendered by UserProfile.render_for_prompt


# ======================================================================
# Direkt-vom-Workspace-Load (Integration)
# ======================================================================

class TestWorkspaceIntegration:
    def test_load_from_workspace(self, tmp_path: Path) -> None:
        ws = Workspace.ensure(tmp_path / "ws2")
        profile = UserProfile.load(ws.user_path)
        # Template hat name=null
        assert profile.get("identity", "name") is None
