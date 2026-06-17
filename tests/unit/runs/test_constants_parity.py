from jarvis.runs.constants import (
    SLO_STATUSES, SLO_OK, SLO_WARN, SLO_BREACH,
    RUN_DECISION_KINDS, DECISION_TIER, DECISION_ROUTE, DECISION_RISK,
    DECISION_BRAIN, DECISION_MISSION, DECISION_FALLBACK,
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
