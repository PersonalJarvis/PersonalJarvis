"""The rulebook of the Jarvis Verse level system is consistent and well paced."""

from __future__ import annotations

from jarvis.progression.rules import (
    MAX_LEVEL,
    REWARDS,
    RULES,
    SLOTS,
    SUBJECT_KINDS,
    TITLES,
    WORLD_ACTIONS,
    level_for_xp,
    progress_for_xp,
    rewards_between,
    subject_kind,
    title_for,
    total_xp_for_level,
    unlocked_rewards,
    xp_to_next,
)


def test_the_first_level_comes_after_a_handful_of_actions():
    assert xp_to_next(1) == 40
    assert level_for_xp(0) == 1
    assert level_for_xp(39) == 1
    assert level_for_xp(40) == 2


def test_every_step_costs_more_than_the_one_before_and_reads_in_fives():
    steps = [xp_to_next(level) for level in range(1, MAX_LEVEL)]
    assert all(step % 5 == 0 for step in steps)
    assert all(later > earlier for earlier, later in zip(steps, steps[1:], strict=False))
    assert xp_to_next(MAX_LEVEL) == 0


def test_pacing_matches_the_design_targets():
    # ~200 XP on an ordinary day: level 10 inside two weeks, the cap after months.
    assert total_xp_for_level(10) < 200 * 14
    assert 200 * 120 < total_xp_for_level(MAX_LEVEL) < 200 * 365


def test_level_and_progress_round_trip_at_every_boundary():
    for level in range(1, MAX_LEVEL + 1):
        start = total_xp_for_level(level)
        assert level_for_xp(start) == level
        progress = progress_for_xp(start)
        assert (progress.level, progress.into_level) == (level, 0)
        if level > 1:
            assert level_for_xp(start - 1) == level - 1
    capped = progress_for_xp(total_xp_for_level(MAX_LEVEL) + 10_000)
    assert capped.level == MAX_LEVEL and capped.for_next == 0


def test_rules_have_unique_sources_and_pay_a_known_kind():
    sources = [rule.source for rule in RULES]
    assert len(sources) == len(set(sources))
    for rule in RULES:
        assert rule.kind in SUBJECT_KINDS
        assert rule.xp > 0
        assert rule.trigger in ("server", "world")
        # A world action is reported by the client: it must be metered.
        if rule.trigger == "world":
            assert rule.cooldown_s > 0 or rule.once_per_ref, rule.source
    assert "daily_visit" in WORLD_ACTIONS and "chat_turn" not in WORLD_ACTIONS


def test_rewards_are_unique_and_every_kind_gets_one_per_slot_family():
    ids = [reward.reward_id for reward in REWARDS]
    assert len(ids) == len(set(ids))
    for reward in REWARDS:
        assert reward.slot in SLOTS
        assert reward.reward_id.startswith(reward.slot + "_")
        for kind in SUBJECT_KINDS:
            level = reward.level_for(kind)
            assert level is None or 2 <= level <= MAX_LEVEL
    for kind in SUBJECT_KINDS:
        slots = {r.slot for r in unlocked_rewards(kind, MAX_LEVEL)}
        assert {"frame", "trail", "aura"} <= slots


def test_rewards_between_names_only_what_the_climb_crossed():
    assert [r.reward_id for r in rewards_between("person", 1, 2)] == ["trail_footprints"]
    at_ten = {r.reward_id for r in rewards_between("person", 9, 10)}
    assert at_ten == {"frame_silver", "gadget_drone"}
    assert rewards_between("person", 10, 10) == []


def test_titles_start_at_level_one_and_climb():
    for kind in SUBJECT_KINDS:
        bands = TITLES[kind]
        assert bands[0][0] == 1
        assert [lvl for lvl, _ in bands] == sorted(lvl for lvl, _ in bands)
    assert title_for("person", 1) == "newcomer"
    assert title_for("person", 12) == "operator"
    assert title_for("pet", MAX_LEVEL) == "mythic"


def test_subject_ids_name_their_kind():
    assert subject_kind("person") == "person"
    assert subject_kind("agent:scout") == "agent"
    assert subject_kind("pet:ember") == "pet"
    assert subject_kind("pet:") is None
    assert subject_kind("robot:x") is None
