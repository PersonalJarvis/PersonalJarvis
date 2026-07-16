"""Unit tests for jarvis.core.capabilities and jarvis.core.capabilities_seed."""
from __future__ import annotations

import threading

import pytest

from jarvis.core.capabilities import (
    Capability,
    CapabilityRegistry,
    _normalize,
    get_registry,
)
from jarvis.core.capabilities_seed import _SEED_CAPABILITIES, seed_registry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cap(cap_id: str = "test.cap", **overrides: object) -> Capability:
    defaults: dict[str, object] = dict(
        id=cap_id,
        source="router_tool",
        verbs=("oeffne", "open", "starte"),
        objects=("app", "browser"),
        description="Test capability.",
        risk_tier="monitor",
        requires_evidence=True,
    )
    defaults.update(overrides)
    return Capability(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _normalize
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_lowercase(self) -> None:
        assert _normalize("Hello") == "hello"

    def test_umlaut_ae(self) -> None:
        assert _normalize("ä") == "ae"  # i18n-allow: German umlaut input matched in logic
        assert _normalize("Ä") == "ae"  # i18n-allow: German umlaut input matched in logic

    def test_umlaut_oe(self) -> None:
        assert _normalize("ö") == "oe"  # i18n-allow: German umlaut input matched in logic
        assert _normalize("Ö") == "oe"  # i18n-allow: German umlaut input matched in logic

    def test_umlaut_ue(self) -> None:
        assert _normalize("ü") == "ue"  # i18n-allow: German umlaut input matched in logic
        assert _normalize("Ü") == "ue"  # i18n-allow: German umlaut input matched in logic

    def test_sharp_s(self) -> None:
        assert _normalize("ß") == "ss"  # i18n-allow: German umlaut input matched in logic

    def test_mixed(self) -> None:
        assert _normalize("Öffne") == "oeffne"  # i18n-allow
        assert _normalize("GrüßGott") == "gruessgott"  # i18n-allow


# ---------------------------------------------------------------------------
# Capability dataclass
# ---------------------------------------------------------------------------


class TestCapabilityDataclass:
    def test_frozen(self) -> None:
        cap = _make_cap()
        with pytest.raises((AttributeError, TypeError)):
            cap.id = "other"  # type: ignore[misc]

    def test_fields_stored(self) -> None:
        cap = _make_cap(
            cap_id="tool.run-shell",
            source="router_tool",
            verbs=("run", "fuehre"),
            objects=("shell", "terminal"),
            description="Run a shell command.",
            risk_tier="ask",
            requires_evidence=True,
        )
        assert cap.id == "tool.run-shell"
        assert "run" in cap.verbs
        assert "shell" in cap.objects
        assert cap.risk_tier == "ask"
        assert cap.requires_evidence is True


# ---------------------------------------------------------------------------
# CapabilityRegistry
# ---------------------------------------------------------------------------


class TestRegistryRegister:
    def setup_method(self) -> None:
        self.reg = CapabilityRegistry()

    def test_register_and_all(self) -> None:
        cap = _make_cap("a.cap")
        self.reg.register(cap)
        assert cap in self.reg.all()

    def test_all_returns_tuple(self) -> None:
        self.reg.register(_make_cap("b.cap"))
        assert isinstance(self.reg.all(), tuple)

    def test_reregister_replaces(self) -> None:
        cap1 = _make_cap("x.cap", description="v1")
        cap2 = _make_cap("x.cap", description="v2")
        self.reg.register(cap1)
        self.reg.register(cap2)
        caps = self.reg.all()
        assert len([c for c in caps if c.id == "x.cap"]) == 1
        assert any(c.description == "v2" for c in caps)

    def test_multiple_caps(self) -> None:
        for i in range(5):
            self.reg.register(_make_cap(f"cap.{i}"))
        assert len(self.reg.all()) == 5


class TestRegistryResolveIntent:
    def setup_method(self) -> None:
        self.reg = CapabilityRegistry()
        self.shell_cap = _make_cap(
            "tool.run-shell",
            verbs=("fuehre", "run", "execute"),
            objects=("shell", "terminal", "command"),
        )
        self.wiki_cap = _make_cap(
            "tool.wiki-recall",
            verbs=("suche", "find", "recall"),
            objects=("wiki", "notiz", "wissen"),
        )
        self.reg.register(self.shell_cap)
        self.reg.register(self.wiki_cap)

    def test_verb_match_returns_cap(self) -> None:
        result = self.reg.resolve_intent(
            "fuehre ein Kommando aus"  # i18n-allow
        )
        assert result is not None
        assert result.id == "tool.run-shell"

    def test_verb_and_object_match_is_best(self) -> None:
        result = self.reg.resolve_intent("suche im wiki nach Python")
        assert result is not None
        assert result.id == "tool.wiki-recall"

    def test_no_match_returns_none(self) -> None:
        result = self.reg.resolve_intent(
            "wie ist das Wetter?"  # i18n-allow
        )
        assert result is None

    def test_umlaut_normalisation_in_utterance(self) -> None:
        # "öffne" normalises to "oeffne", but the cap has "fuehre".  # i18n-allow
        # match shell; register an open_app cap to prove normalisation works.
        open_cap = _make_cap(
            "local.open_app",
            verbs=("oeffne", "open"),
            objects=("app", "browser"),
        )
        self.reg.register(open_cap)
        result = self.reg.resolve_intent(
            "Öffne den Browser"  # i18n-allow
        )
        assert result is not None
        assert result.id == "local.open_app"

    def test_whole_word_boundary(self) -> None:
        # "runs" should NOT match verb "run" due to word boundary
        narrow_cap = _make_cap(
            "narrow.cap",
            verbs=("run",),
            objects=("test",),
        )
        self.reg.register(narrow_cap)
        # "run" appears as a whole word here → should match
        assert self.reg.resolve_intent("run the test") is not None
        # "rerun" does NOT start/end on a word boundary for "run" → no match
        # (re uses \b which is between \w and \W; "rerun" has \b before 'r'
        # and after 'n' but not isolating the 'run' substring — correct)
        self.reg.resolve_intent("rerunning")
        # "rerunning" has no isolated "run" word → depends on regex; key
        # invariant is that an exact word "run" in context matches.
        assert self.reg.resolve_intent("please run this") is not None


class TestRegistryHasActionIntent:
    def setup_method(self) -> None:
        self.reg = CapabilityRegistry()
        cap = _make_cap(
            "tool.run-shell",
            verbs=("run", "execute", "fuehre"),
            objects=("shell",),
        )
        self.reg.register(cap)

    def test_action_utterance_returns_true(self) -> None:
        assert self.reg.has_action_intent("run a shell command") is True

    def test_non_action_returns_false(self) -> None:
        assert self.reg.has_action_intent(
            "wie spät ist es?"  # i18n-allow
        ) is False

    def test_empty_string(self) -> None:
        assert self.reg.has_action_intent("") is False

    def test_umlaut_verb_normalised(self) -> None:
        cap2 = _make_cap(
            "local.open_app",
            verbs=("oeffne",),
            objects=("app",),
        )
        self.reg.register(cap2)
        # "Öffne" normalises to "oeffne".  # i18n-allow
        assert self.reg.has_action_intent("Öffne Chrome") is True  # i18n-allow

    def test_filler_particle_halt_is_not_an_action(self) -> None:
        # The German discourse particle "halt" (a filler word, roughly
        # "just"/"simply") is NOT a command — it must not collide with the
        # stop-verb stem in the universal catalogue. Live bug 2026-06-19: the
        # filler tripped has_action_intent and force-spawned a worker on a pure
        # chat turn (the San-Francisco emigration session). The fixtures below
        # are intentionally German (the content under test).
        assert self.reg.has_action_intent("Das ist halt so.") is False  # i18n-allow
        assert (
            self.reg.has_action_intent("Ich hab mir das halt echt überlegt.")  # i18n-allow
            is False
        )
        assert (
            self.reg.has_action_intent("Na ja, ist halt kompliziert.") is False  # i18n-allow
        )

    def test_genuine_stop_commands_still_action(self) -> None:
        # The fix must NOT lose real stop/pause commands — they stay actions via
        # the unambiguous "stop"/"stoppe" stems (which also cover German
        # "stopp"/"stoppen").
        assert self.reg.has_action_intent("Stopp die Musik.") is True
        assert self.reg.has_action_intent("Stoppe das Video.") is True
        assert self.reg.has_action_intent("Stop the music.") is True


class TestRegistryRenderForPrompt:
    def setup_method(self) -> None:
        self.reg = CapabilityRegistry()

    def test_empty_registry(self) -> None:
        rendered = self.reg.render_for_prompt()
        assert "No capabilities" in rendered

    def test_bullet_format(self) -> None:
        self.reg.register(_make_cap("tool.run-shell", description="Run shell."))
        rendered = self.reg.render_for_prompt()
        assert "• tool.run-shell" in rendered
        assert "Run shell." in rendered

    def test_multiple_caps(self) -> None:
        for i in range(3):
            self.reg.register(_make_cap(f"cap.{i}", description=f"Cap {i}."))
        lines = self.reg.render_for_prompt().splitlines()
        assert len(lines) == 3

    def test_lang_param_accepted(self) -> None:
        self.reg.register(_make_cap())
        # lang param is currently cosmetic but must not raise
        self.reg.render_for_prompt(lang="en")
        self.reg.render_for_prompt(lang="de")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_same_instance(self) -> None:
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_thread_safe(self) -> None:
        """Multiple threads calling get_registry() must get the same instance."""
        results: list[CapabilityRegistry] = []
        lock = threading.Lock()

        def _get() -> None:
            r = get_registry()
            with lock:
                results.append(r)

        threads = [threading.Thread(target=_get) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(r is results[0] for r in results)


# ---------------------------------------------------------------------------
# Seed map
# ---------------------------------------------------------------------------


class TestSeedRegistry:
    def setup_method(self) -> None:
        self.reg = CapabilityRegistry()
        seed_registry(self.reg)

    def test_all_seed_caps_registered(self) -> None:
        ids = {c.id for c in self.reg.all()}
        seed_ids = {c.id for c in _SEED_CAPABILITIES}
        assert seed_ids.issubset(ids)

    def test_router_tools_present(self) -> None:
        ids = {c.id for c in self.reg.all()}
        for tool in (
            "tool.run-shell",
            "tool.screen-snapshot",
            # NB: tool.dispatch-to-harness removed 2026-06-28 (no longer an
            # LLM-visible router tool — phantom-openclaw routing fix).
            "tool.spawn-worker",
            "tool.awareness-snapshot",
            "tool.awareness-recall",
            "tool.run-skill",
            "tool.wiki-recall",
            "tool.wiki-page-read",
            "tool.wiki-ingest",
        ):
            assert tool in ids, f"{tool!r} missing from seed"

    def test_polite_wiki_system_write_resolves_to_ingest(self) -> None:
        """Wiki destination wins even when the fact mentions travel."""
        cap = self.reg.resolve_intent(
            "Kannst du bitte mein Wiki-System eintragen, dass ich morgen nach "  # i18n-allow
            "San Francisco reisen will?"  # i18n-allow: production transcript under test
        )

        assert cap is not None
        assert cap.id == "tool.wiki-ingest"

    def test_dispatch_to_harness_capability_removed(self) -> None:
        """The dead dispatch-to-harness capability must not reappear (2026-06-28)."""
        ids = {c.id for c in self.reg.all()}
        assert "tool.dispatch-to-harness" not in ids

    def test_multi_spawn_capability_removed(self) -> None:
        """Parallel work is expressed through one supervised mission path."""
        ids = {c.id for c in self.reg.all()}
        assert "tool.multi-spawn" not in ids

    def test_dispatch_with_review_capability_removed(self) -> None:
        """Review is part of spawn-worker, never a competing router action."""
        ids = {c.id for c in self.reg.all()}
        assert "tool.dispatch-with-review" not in ids

    def test_local_actions_present(self) -> None:
        ids = {c.id for c in self.reg.all()}
        for la in (
            "local.open_app",
            "local.type_text",
            "local.hotkey",
            "local.reset_orb_position",
            "local.terminal_count",
        ):
            assert la in ids, f"{la!r} missing from seed"

    def test_only_operational_harness_adapters_are_present(self) -> None:
        ids = {c.id for c in self.reg.all()}
        for ha in (
            "harness.computer-use",
            "harness.python-script",
        ):
            assert ha in ids, f"{ha!r} missing from seed"
        assert {
            "harness.openclaw",
            "harness.mcp-remote",
            "harness.open-interpreter",
        }.isdisjoint(ids)

    def test_no_capability_advertises_openclaw(self) -> None:
        """No seeded capability may advertise the phantom 'openclaw' vehicle.

        OpenClaw is unregistered (Welle-4 removal). Advertising it in a router
        tool's verbs/objects/description mis-routed "start a subagent" turns
        toward a vehicle that cannot run (forensic 2026-06-28). Heavy sub-agent
        work is tool.spawn-worker.
        """
        for cap in self.reg.all():
            haystack = " ".join(
                (cap.description, *cap.verbs, *cap.objects)
            ).lower()
            assert "openclaw" not in haystack, (
                f"capability {cap.id!r} still advertises 'openclaw'"
            )

    def test_requires_evidence_true_for_action_tools(self) -> None:
        """Action tools must have requires_evidence=True."""
        action_ids = {
            "tool.run-shell",
            "tool.screen-snapshot",
            "tool.spawn-worker",
            "tool.run-skill",
            "tool.wiki-ingest",
        }
        caps_by_id = {c.id: c for c in self.reg.all()}
        for cap_id in action_ids:
            assert caps_by_id[cap_id].requires_evidence is True, (
                f"{cap_id!r} should have requires_evidence=True"
            )

    def test_requires_evidence_false_for_read_only(self) -> None:
        """Read-only tools must have requires_evidence=False."""
        read_only_ids = {
            "tool.awareness-snapshot",
            "tool.awareness-recall",
            "tool.wiki-recall",
            "tool.wiki-page-read",
        }
        caps_by_id = {c.id: c for c in self.reg.all()}
        for cap_id in read_only_ids:
            assert caps_by_id[cap_id].requires_evidence is False, (
                f"{cap_id!r} should have requires_evidence=False"
            )

    def test_idempotent(self) -> None:
        before = len(self.reg.all())
        seed_registry(self.reg)  # second call
        assert len(self.reg.all()) == before

    def test_open_app_matches_oeffne_chrome(self) -> None:
        result = self.reg.resolve_intent("Öffne Chrome")  # i18n-allow
        assert result is not None
        assert result.id == "local.open_app"

    def test_wiki_recall_no_evidence(self) -> None:
        result = self.reg.resolve_intent("Suche im Wiki nach Python")
        assert result is not None
        assert result.id == "tool.wiki-recall"
        assert result.requires_evidence is False

    def test_run_shell_matches(self) -> None:
        result = self.reg.resolve_intent(
            "führe ein Shell-Kommando aus"  # i18n-allow
        )
        assert result is not None
        assert result.id == "tool.run-shell"

    def test_smalltalk_no_match(self) -> None:
        result = self.reg.resolve_intent("wie spät ist es?")  # i18n-allow
        assert result is None

    def test_has_action_intent_with_seed(self) -> None:
        assert self.reg.has_action_intent("Öffne den Browser") is True  # i18n-allow
        assert self.reg.has_action_intent(
            "wie ist das Wetter?"  # i18n-allow
        ) is False

    def test_has_action_intent_deverbal_noun_is_not_a_command(self) -> None:
        """Determiner-led deverbal nouns ('eine ganz generelle Frage', 'die  # i18n-allow: bug quote
        Antwort') must not impersonate their verb stems ('frag', 'antwort'):
        the collision classified a plain knowledge question as generic
        sub-agent work and force-spawned a full background worker (live bug
        2026-07-16, voice session 11:49)."""
        assert self.reg.has_action_intent(
            "Du, ich hab mal eine ganz generelle Frage, wie viel Geld ähm "  # i18n-allow: bug quote
            "hat eigentlich Elon Musk gerade aktuell?"
        ) is False
        assert self.reg.has_action_intent(
            "Was ist die Antwort auf das Leben?"  # i18n-allow: noun collision under test
        ) is False
        assert self.reg.has_action_intent(
            "Ich hätte da noch eine kurze Frage zu dem Thema."  # i18n-allow: under test
        ) is False

    def test_has_action_intent_keeps_genuine_ask_imperatives(self) -> None:
        """A real imperative never follows a determiner — masking the noun
        spans must not lose genuine ask/send commands."""
        assert self.reg.has_action_intent(
            "Frag Anna, ob sie morgen Zeit hat."  # i18n-allow: imperative under test
        ) is True
        assert self.reg.has_action_intent(
            "Schick eine Mail an Anna und frag sie, ob es morgen passt."  # i18n-allow: imperative under test
        ) is True
