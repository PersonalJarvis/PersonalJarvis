"""Initiative: the assistant brings grounded ideas without taking unrequested actions.

Locks the three pieces the 2026-10-07 change added:

* the rule per level (``jarvis.brain.proactivity.directive``) and the one
  boundary every level that allows ideas must carry: an idea never
  authorises sending, contacting, spending, deleting, changing settings or
  starting background work;
* the dated plans from the notebooks that ground an idea (real notebook
  files under ``tmp_path``);
* the wiring into every surface the assistant speaks through — the brain's
  system prompt (voice and chat, including subscription CLI seats, which
  render the same prompt), the realtime session instructions and GPT-Live —
  and its absence on a society agent's own turn.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import jarvis.core.config as core_config
from jarvis.brain import proactivity
from jarvis.brain.manager import _TURN_OVERRIDE, BrainManager
from jarvis.brain.turn_override import TurnOverride
from jarvis.core.config import BrainConfig, load_config
from jarvis.core.self_mod.registry import SelfModRegistry
from jarvis.memory.learning import notebook as notebook_module
from jarvis.memory.learning.notebook import JarvisNotebook, note_dates

TODAY = date(2026, 10, 7)

#: Phrases of the boundary every idea-allowing rule must keep.
_BOUNDARY = ("never authorises an action", "sends a message", "spends money", "deletes")


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(core_config, "DATA_DIR", tmp_path / "data")
    proactivity.reset_applied_level()
    notebook_module.set_active(None)
    yield
    proactivity.reset_applied_level()
    notebook_module.set_active(None)


def _cfg(level: str = "balanced") -> SimpleNamespace:
    return SimpleNamespace(brain=SimpleNamespace(proactivity=level))


# ── the level ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("off", "off"),
        (" HIGH ", "high"),
        ("balanced", "balanced"),
        ("loud", "balanced"),
        (None, "balanced"),
        (False, "off"),
        ("disabled", "off"),
    ],
)
def test_an_unknown_level_falls_back_to_the_default(raw: object, expected: str) -> None:
    assert proactivity.normalize_level(raw) == expected


def test_the_config_answers_until_a_level_is_applied_live() -> None:
    assert proactivity.current_level(_cfg("off")) == "off"
    assert proactivity.current_level(None) == "balanced"
    proactivity.apply_level("high")
    assert proactivity.current_level(_cfg("off")) == "high"


def test_a_typo_in_the_config_file_never_bricks_the_backend() -> None:
    assert BrainConfig(proactivity="Proactive!").proactivity == "balanced"
    assert BrainConfig(proactivity="OFF").proactivity == "off"
    assert BrainConfig(proactivity=False).proactivity == "off"
    assert BrainConfig(proactivity="none").proactivity == "off"
    assert BrainConfig().proactivity == "balanced"


def test_voice_and_chat_can_change_the_level_without_a_restart() -> None:
    spec = SelfModRegistry.require_spec("brain.proactivity")
    assert spec.risk_tier == "safe"
    assert spec.needs_restart is False


# ── the rule ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("level", ["balanced", "high"])
@pytest.mark.parametrize("compact", [False, True])
def test_every_idea_allowing_rule_keeps_actions_behind_a_yes(level: str, compact: bool) -> None:
    text = proactivity.directive(level, compact=compact)
    if compact:
        for phrase in ("never authorise actions", "sends", "spends", "deletes"):
            assert phrase in text
        assert "until they say yes" in text
        assert "bare or unclear answer is not a yes" in text
    else:
        for phrase in _BOUNDARY:
            assert phrase in text
        assert "starts agents, background work or schedules" in text
        assert "approval prompts still apply" in text
    # A clear yes to an exact offer is the request (no second round of asking),
    # but never a bare yes, and never past a tool's own approval prompt.
    assert "approval prompts still apply" in text
    assert "not ask" in text


def test_balanced_allows_one_grounded_idea_and_stays_quiet_on_small_talk() -> None:
    text = proactivity.directive("balanced")
    assert "At most one such addition per reply" in text
    assert "most replies need none" in text
    assert "greetings, small talk" in text
    assert "never invent" in text
    assert "not as a question" in text
    assert "standing instructions win" in text
    # Lookups the request needs may run; nothing that writes.
    assert "only reads" in text and "easy to undo" not in text


def test_high_allows_two_ideas_and_a_heads_up_on_close_deadlines() -> None:
    text = proactivity.directive("high")
    assert "Up to two" in text
    assert "due within two days" in text


def test_off_adds_nothing_unrequested() -> None:
    for compact in (False, True):
        text = proactivity.directive("off", compact=compact)
        assert "suggestions" in text and "off" in text
        assert "At most one" not in text


def test_the_rule_follows_the_live_level_by_default() -> None:
    proactivity.apply_level("off")
    assert proactivity.directive() == proactivity.directive("off")


# ── dated plans ────────────────────────────────────────────────────────────


def test_dates_are_read_from_iso_and_day_first_forms() -> None:
    assert note_dates("Launch on 2026-10-09, review 12.10.2026.") == [
        date(2026, 10, 9),
        date(2026, 10, 12),
    ]
    assert note_dates("Invalid 2026-13-40 and 31.02.2026, version 2026-10-091") == []


def test_the_day_an_explicit_note_was_saved_is_not_a_plan() -> None:
    assert note_dates("2026-10-07 (asked to remember): call the bank") == []
    assert note_dates("2026-10-01 (asked to remember): the dentist is on 2026-10-08") == [
        date(2026, 10, 8)
    ]


def test_upcoming_lines_keep_the_next_two_weeks_nearest_first() -> None:
    notes = [
        (date(2026, 10, 20), "The user's flat inspection is on 2026-10-20."),
        (date(2026, 10, 7), "The tax return is due on 2026-10-07."),
        (date(2026, 10, 1), "The user moved on 2026-10-01."),  # past
        (date(2026, 10, 30), "The conference starts on 2026-10-30."),  # beyond the horizon
        (date(2026, 10, 8), "The user's  launch\nrehearsal is on 2026-10-08."),
        (date(2026, 10, 8), "The user's launch rehearsal is on 2026-10-08."),  # repeat
    ]
    lines = proactivity.upcoming_lines(notes, today=TODAY)
    assert lines == [
        "- today (Wednesday, 2026-10-07): The tax return is due on 2026-10-07.",
        "- tomorrow (Thursday, 2026-10-08): The user's launch rehearsal is on 2026-10-08.",
        "- in 13 days (Tuesday, 2026-10-20): The user's flat inspection is on 2026-10-20.",
    ]
    assert len(proactivity.upcoming_lines(notes, today=TODAY, limit=1)) == 1


def _book_with_plans(tmp_path: Path) -> JarvisNotebook:
    book = JarvisNotebook(tmp_path / "vault", budgets={"user": 1_000, "memory": 1_000})
    book.apply(
        target="user",
        operation="add",
        text="The user is preparing a product launch for 2026-10-09.",
        importance=8,
    )
    book.apply(target="user", operation="add", text="The user lives in Hamburg.")
    notebook_module.set_active(book)
    return book


def test_the_notebooks_dated_plans_reach_the_block(tmp_path: Path) -> None:
    book = _book_with_plans(tmp_path)
    assert book.dated_notes() == [
        (date(2026, 10, 9), "The user is preparing a product launch for 2026-10-09.")
    ]
    block = proactivity.upcoming_block("balanced", today=TODAY)
    assert block.startswith("## Coming up (dated plans from your notes)")
    assert "in 2 days (Friday, 2026-10-09): The user is preparing a product launch" in block
    assert "Hamburg" not in block


def test_an_edit_in_the_notebook_reaches_the_next_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = _book_with_plans(tmp_path)
    assert "2026-10-09" in proactivity.upcoming_block("balanced", today=TODAY)
    entry = book.entries()["user"][0]
    book.apply(
        target="user",
        operation="replace",
        entry_id=entry.id,
        text="The product launch moved to 2026-10-12.",
    )
    monkeypatch.setattr(notebook_module, "_STAT_INTERVAL_S", 0.0)
    block = proactivity.upcoming_block("balanced", today=TODAY)
    assert "2026-10-12" in block and "2026-10-09" not in block


def test_no_dated_plans_and_initiative_off_add_no_block(tmp_path: Path) -> None:
    assert proactivity.upcoming_block("high", today=TODAY) == ""  # no notebook running
    _book_with_plans(tmp_path)
    assert proactivity.upcoming_block("off", today=TODAY) == ""
    assert proactivity.upcoming_notes(today=TODAY)[0][0] == date(2026, 10, 9)


# ── wiring: the brain's system prompt (voice + chat + CLI seats) ───────────


def _manager(level: str = "balanced") -> BrainManager:
    m = BrainManager.__new__(BrainManager)
    m._soul = None
    m._user_profile = None
    m._people = None
    m._core_memory = None
    m._system_prompt_extra = ""
    m._wiki_context_suffix = ""
    m._reply_language = "auto"
    cfg = load_config()
    cfg.performance.cache_optimized_prompt = False
    cfg.brain.proactivity = level
    m._config = cfg
    return m


def test_the_brain_prompt_carries_the_configured_rule(tmp_path: Path) -> None:
    _book_with_plans(tmp_path)
    prompt = _manager("balanced")._build_system_prompt()
    assert proactivity.directive("balanced") in prompt
    assert "## Coming up (dated plans from your notes)" in prompt
    off = _manager("off")._build_system_prompt()
    assert proactivity.directive("off") in off
    assert "## Coming up" not in off


def test_cache_optimized_prompts_keep_dated_plans_out_of_the_cached_prefix(
    tmp_path: Path,
) -> None:
    _book_with_plans(tmp_path)
    m = _manager("high")
    m._config.performance.cache_optimized_prompt = True
    assert proactivity.directive("high") in m._build_system_prompt()
    assert "## Coming up" not in m._build_system_prompt()
    assert "## Coming up (dated plans from your notes)" in m._build_turn_context()


def test_a_society_agents_turn_gets_no_initiative_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = _manager("high")
    monkeypatch.setattr(BrainManager, "_render_live_tool_block", lambda self: "")
    token = _TURN_OVERRIDE.set(
        TurnOverride(
            provider="p",
            system_extra="You are Nova, a research agent.",
            tool_context={"tool_origin": "society"},
        )
    )
    try:
        prompt = m._build_system_prompt()
    finally:
        _TURN_OVERRIDE.reset(token)
    assert "You are Nova" in prompt
    assert "INITIATIVE" not in prompt


# ── wiring: realtime voice and GPT-Live ────────────────────────────────────


def test_realtime_instructions_carry_the_rule_and_the_dated_plans(tmp_path: Path) -> None:
    from jarvis.realtime.session import _session_instructions

    _book_with_plans(tmp_path)
    full = _session_instructions("de", proactivity_level="balanced")
    assert proactivity.directive("balanced") in full
    assert "## Coming up (dated plans from your notes)" in full
    compact = _session_instructions("de", proactivity_level="high", compact=True)
    assert proactivity.directive("high", compact=True) in compact
    # Compact keeps the static rule in the prefix and the dated plans in the tail.
    assert compact.index("INITIATIVE") < compact.index("## Coming up")
    off = _session_instructions("de", proactivity_level="off")
    assert proactivity.directive("off") in off
    assert "## Coming up" not in off


def test_gpt_live_identity_carries_the_rule(tmp_path: Path) -> None:
    from jarvis.live.session import _identity

    _book_with_plans(tmp_path)
    cfg = load_config()
    cfg.brain.proactivity = "high"
    text = _identity(cfg)
    assert proactivity.directive("high") in text
    assert "2026-10-09" in text


async def test_a_voice_or_chat_change_applies_on_the_next_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``set_config_value brain.proactivity`` goes through Self-Mod, which
    dispatches ``ConfigReloaded``; the manager makes the new level live for
    every surface without a restart."""
    import time
    from uuid import uuid4

    from jarvis.core.bus import EventBus
    from jarvis.core.events import ConfigReloaded

    target = tmp_path / "jarvis.toml"
    target.write_text('[brain]\nproactivity = "off"\n', encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(target))
    bus = EventBus()
    m = _manager("high")
    m._bus = bus
    m.attach_to_bus()

    reload = ConfigReloaded(
        trace_id=uuid4(),
        timestamp_ns=time.time_ns(),
        source_layer="self_mod",
        changed_keys=("ui.theme",),
    )
    await bus.publish(reload)
    assert proactivity.current_level(m._config) == "high"

    await bus.publish(
        ConfigReloaded(
            trace_id=uuid4(),
            timestamp_ns=time.time_ns(),
            source_layer="self_mod",
            changed_keys=("brain.proactivity",),
        )
    )
    assert proactivity.current_level() == "off"
    assert proactivity.directive("off") in m._build_system_prompt()
