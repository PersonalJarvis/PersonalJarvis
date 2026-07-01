"""Unit tests for Phase A4 ``WorkingSet`` (jarvis/awareness/working_set.py).

Spec: JARVIS_AWARENESS_PLAN.md §8.

Tests:
- LRU eviction at full capacity (>5 slots).
- Re-promotion on re-activation (A → B → A).
- ``set_episode`` linkage with the active slot.
- Eviction deletes NO episodes (only the RAM pointer).
- Render format for the system prompt (multi-context vs. single-context).

Convention: fakes instead of mocks, pure unit tests without async/IO.
"""
from __future__ import annotations

from jarvis.awareness.context import Context
from jarvis.awareness.working_set import DEFAULT_MAX_SLOTS, WorkingSet


def _ctx(
    root: str,
    *,
    label: str = "task",
    episode_id: int | None = None,
    last_seen_ns: int = 0,
    process: str = "",
) -> Context:
    return Context(
        project_root=root,
        task_label=label,
        last_episode_id=episode_id,
        last_seen_ns=last_seen_ns,
        process_name=process,
    )


# ---- Basic Lifecycle -------------------------------------------------------


def test_working_set_starts_empty() -> None:
    ws = WorkingSet()
    assert ws.size == 0
    assert ws.current is None
    assert ws.contexts() == []


def test_default_max_slots_is_five() -> None:
    """Plan §8: Working Set max 5 LRU-Slots."""
    assert DEFAULT_MAX_SLOTS == 5
    assert WorkingSet().max_slots == 5


def test_max_slots_validation() -> None:
    import pytest

    with pytest.raises(ValueError):
        WorkingSet(max_slots=0)
    with pytest.raises(ValueError):
        WorkingSet(max_slots=-1)


# ---- Observe + LRU --------------------------------------------------------


def test_observe_inserts_new_context() -> None:
    ws = WorkingSet()
    ctx = _ctx("repo-a", label="main.py")
    evicted = ws.observe(ctx)
    assert evicted is None
    assert ws.size == 1
    assert "repo-a" in ws
    assert ws.get("repo-a") == ctx


def test_observe_promotes_existing_context() -> None:
    """Re-Promotion: bekannter project_root wandert ans Ende der LRU."""
    ws = WorkingSet(max_slots=3)
    a = _ctx("a", label="a-task")
    b = _ctx("b", label="b-task")
    c = _ctx("c", label="c-task")
    ws.observe(a)
    ws.observe(b)
    ws.observe(c)
    # c ist juengster, a ist aeltester.
    assert [ctx.project_root for ctx in ws.contexts()] == ["c", "b", "a"]

    # Re-Activation von a → a wandert ans MRU-Ende.
    ws.observe(_ctx("a", label="a-task-v2"))
    assert [ctx.project_root for ctx in ws.contexts()] == ["a", "c", "b"]


def test_observe_evicts_oldest_when_full() -> None:
    """Plan §8 Eviction: > max_slots → aeltester fliegt raus."""
    ws = WorkingSet(max_slots=3)
    ws.observe(_ctx("a"))
    ws.observe(_ctx("b"))
    ws.observe(_ctx("c"))
    # Vierter Context → evictet "a".
    evicted = ws.observe(_ctx("d"))
    assert evicted is not None
    assert evicted.project_root == "a"
    assert ws.size == 3
    assert "a" not in ws
    assert "d" in ws


def test_observe_seven_contexts_with_max_five_evicts_oldest_two() -> None:
    """7 context switches with max=5: the oldest slot flies out, 5 remain.

    Acceptance criterion: "Test with 7 context switches: the oldest
    context entry flies out of the working set, all 7 episodes in the
    DB unchanged."
    """
    ws = WorkingSet(max_slots=5)
    inserted = ["c1", "c2", "c3", "c4", "c5", "c6", "c7"]
    evicted_roots: list[str] = []
    for r in inserted:
        evicted = ws.observe(_ctx(r))
        if evicted is not None:
            evicted_roots.append(evicted.project_root)
    # c1 is evicted by c6, c2 by c7.
    assert evicted_roots == ["c1", "c2"]
    assert ws.size == 5
    assert [ctx.project_root for ctx in ws.contexts()] == ["c7", "c6", "c5", "c4", "c3"]


def test_promote_a_then_b_then_a() -> None:
    """A→B→A: A landet wieder oben in MRU, snapshot zeigt A als current."""
    ws = WorkingSet(max_slots=5)
    a = _ctx("a", label="a-work")
    b = _ctx("b", label="b-work")
    ws.observe(a)
    ws.observe(b)
    assert ws.current.project_root == "b"

    ws.observe(_ctx("a", label="a-work-resumed"))
    assert ws.current.project_root == "a"
    # task_label from the last observe overwrites
    assert ws.current.task_label == "a-work-resumed"


# ---- set_episode ----------------------------------------------------------


def test_set_episode_updates_existing_slot() -> None:
    ws = WorkingSet()
    ws.observe(_ctx("a"))
    ws.set_episode("a", 42)
    assert ws.get("a").last_episode_id == 42


def test_set_episode_returns_false_for_unknown_root() -> None:
    """If the slot has been evicted, set_episode swallows it silently."""
    ws = WorkingSet(max_slots=2)
    ws.observe(_ctx("a"))
    ws.observe(_ctx("b"))
    ws.observe(_ctx("c"))    # evicts "a"
    assert ws.set_episode("a", 99) is False
    # b and c are unchanged
    assert ws.get("b").last_episode_id is None


def test_set_episode_does_not_promote() -> None:
    """set_episode only updates the episode ID, NOT the LRU order."""
    ws = WorkingSet(max_slots=3)
    ws.observe(_ctx("a"))
    ws.observe(_ctx("b"))
    ws.observe(_ctx("c"))
    # c is current, a is the oldest.
    ws.set_episode("a", 11)
    # Order unchanged: c stays MRU, a stays LRU.
    assert [ctx.project_root for ctx in ws.contexts()] == ["c", "b", "a"]


def test_observe_preserves_existing_episode_when_new_is_none() -> None:
    """Re-Activation ohne neue Episode-ID: alte ID bleibt erhalten."""
    ws = WorkingSet()
    ws.observe(_ctx("a", episode_id=7))
    ws.observe(_ctx("a", episode_id=None))
    assert ws.get("a").last_episode_id == 7


def test_observe_overwrites_episode_when_new_is_set() -> None:
    """When a new context sets a new episode ID, it overwrites the old one."""
    ws = WorkingSet()
    ws.observe(_ctx("a", episode_id=7))
    ws.observe(_ctx("a", episode_id=12))
    assert ws.get("a").last_episode_id == 12


# ---- Render ---------------------------------------------------------------


def test_render_for_prompt_empty_returns_empty_string() -> None:
    assert WorkingSet().render_for_prompt() == ""


def test_render_for_prompt_single_slot_returns_empty_string() -> None:
    """Single-Context = redundant zur "letzte Episode"-Zeile, no render."""
    ws = WorkingSet()
    ws.observe(_ctx("a", label="solo"))
    assert ws.render_for_prompt() == ""


def test_render_for_prompt_multi_slot_includes_all_contexts() -> None:
    ws = WorkingSet(max_slots=5)
    ws.observe(_ctx("repo-jarvis", label="awareness/working_set.py"))
    ws.observe(_ctx("slack.com", label="DMs Sarah"))
    ws.set_episode("repo-jarvis", 42)
    ws.set_episode("slack.com", 43)

    text = ws.render_for_prompt()

    assert "Active contexts" in text
    assert "repo-jarvis" in text
    assert "slack.com" in text
    # Episode IDs are displayed too
    assert "Episode #42" in text
    assert "Episode #43" in text


def test_render_for_prompt_truncates_to_max_chars() -> None:
    ws = WorkingSet(max_slots=5)
    for i in range(5):
        ws.observe(_ctx(
            f"very-long-project-root-{i}-" + "x" * 50,
            label=f"task-with-a-rather-long-label-{i}",
        ))
    text = ws.render_for_prompt(max_chars=80)
    assert len(text) <= 80
    assert text.endswith("…")


# ---- Eviction Hard-Negative: Episodes Survive ------------------------------


def test_eviction_does_not_affect_episode_id_field() -> None:
    """Plan §8 hard negative: eviction = only the RAM pointer goes away, the
    episode stays in the DB. Here: on eviction only the WorkingSet slot
    goes away, the last_episode_id value is still visible on the evicted
    object (verifies that we do not wipe data).
    """
    ws = WorkingSet(max_slots=2)
    ws.observe(_ctx("a"))
    ws.set_episode("a", 100)
    ws.observe(_ctx("b"))
    evicted = ws.observe(_ctx("c"))    # evicts "a"
    assert evicted is not None
    assert evicted.project_root == "a"
    # The episode ID of the evicted slot is still there — a caller could
    # react to it (e.g. audit). We ONLY verify that no delete calls
    # happen — the test itself has no DB.
    assert evicted.last_episode_id == 100
