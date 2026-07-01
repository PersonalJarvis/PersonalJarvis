from jarvis.runs.constants import (
    SLO_STATUSES, SLO_OK, SLO_WARN, SLO_BREACH,
    RUN_DECISION_KINDS, DECISION_TIER, DECISION_ROUTE, DECISION_RISK,
    DECISION_BRAIN, DECISION_MISSION, DECISION_FALLBACK,
    RATIONALE_SOURCES, RATIONALE_MODEL, RATIONALE_RULE,
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
