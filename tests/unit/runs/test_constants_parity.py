from jarvis.runs.constants import (
    SLO_STATUSES, SLO_OK, SLO_WARN, SLO_BREACH,
    RUN_DECISION_KINDS, DECISION_TIER, DECISION_ROUTE, DECISION_RISK,
    DECISION_BRAIN, DECISION_MISSION, DECISION_FALLBACK,
    RATIONALE_SOURCES, RATIONALE_MODEL, RATIONALE_RULE,
    RUN_EVENT_CATEGORIES, EVENT_CATEGORY_BY_KIND,
    EVENT_CAT_LIFECYCLE, EVENT_CAT_SPEECH, EVENT_CAT_BRAIN, EVENT_CAT_TOOL,
    EVENT_CAT_AGENT, EVENT_CAT_VISION, EVENT_CAT_LATENCY, EVENT_CAT_ERROR,
    EVENT_CAT_SYSTEM,
)


def test_slo_statuses_complete_and_stable():
    assert SLO_STATUSES == (SLO_OK, SLO_WARN, SLO_BREACH)
    assert SLO_STATUSES == ("ok", "warn", "breach")


def test_decision_kinds_complete_and_stable():
    assert RUN_DECISION_KINDS == (
        DECISION_TIER, DECISION_ROUTE, DECISION_RISK,
        DECISION_BRAIN, DECISION_MISSION, DECISION_FALLBACK,
    )
    assert set(RUN_DECISION_KINDS) == {
        "tier", "route", "risk", "brain", "mission", "fallback",
    }


def test_rationale_sources_complete_and_stable():
    # The honest-"why" provenance tag. "model" = the brain's own words;
    # "rule" = a deterministic explanation built from a captured fact.
    assert RATIONALE_SOURCES == (RATIONALE_MODEL, RATIONALE_RULE)
    assert set(RATIONALE_SOURCES) == {"model", "rule"}


def test_event_categories_complete_and_stable():
    # Lanes of the raw developer event stream; crosses Python -> TS -> UI.
    assert RUN_EVENT_CATEGORIES == (
        EVENT_CAT_LIFECYCLE, EVENT_CAT_SPEECH, EVENT_CAT_BRAIN, EVENT_CAT_TOOL,
        EVENT_CAT_AGENT, EVENT_CAT_VISION, EVENT_CAT_LATENCY, EVENT_CAT_ERROR,
        EVENT_CAT_SYSTEM,
    )
    assert set(RUN_EVENT_CATEGORIES) == {
        "lifecycle", "speech", "brain", "tool", "agent", "vision",
        "latency", "error", "system",
    }


def test_every_mapped_kind_uses_a_declared_category():
    # A typo in the map would silently drop an event into an unstyled lane.
    unknown = {c for c in EVENT_CATEGORY_BY_KIND.values()
               if c not in RUN_EVENT_CATEGORIES}
    assert unknown == set()
