"""Selbst-Mod-Allowlist enthält KEINE review.*-Pfade (Phase 8.4).

Plan-Referenz: §AD-1 (Allowlist statt Denylist), Plan-§6.4 — review.*
darf NIE per Voice/Chat mutiert werden, nur Datei-Edit + Code-Review.
Das verhindert Constraint-Self-Bypass via Tool-Choice.
"""
from __future__ import annotations

from jarvis.core.self_mod.registry import SelfModRegistry


def test_review_max_iterations_not_in_allowlist() -> None:
    assert SelfModRegistry.is_mutable("review.max_iterations") is False


def test_review_hard_ceiling_not_in_allowlist() -> None:
    assert SelfModRegistry.is_mutable("review.hard_ceiling") is False


def test_review_enabled_not_in_allowlist() -> None:
    assert SelfModRegistry.is_mutable("review.enabled") is False


def test_review_default_rubric_not_in_allowlist() -> None:
    assert SelfModRegistry.is_mutable("review.default_rubric") is False


def test_review_audit_log_path_not_in_allowlist() -> None:
    assert SelfModRegistry.is_mutable("review.audit_log") is False


def test_review_worker_model_not_in_allowlist() -> None:
    assert SelfModRegistry.is_mutable("review.worker_model") is False


def test_review_reviewer_model_not_in_allowlist() -> None:
    assert SelfModRegistry.is_mutable("review.reviewer_model") is False


def test_no_review_path_in_explicit_allowlist() -> None:
    """Defense-in-Depth: kein einziger ALLOWED-Spec hat review-prefix."""
    review_specs = [
        spec for spec in SelfModRegistry.list_all() if spec.path.startswith("review.")
    ]
    assert review_specs == []


def test_get_spec_returns_none_for_review_paths() -> None:
    for path in (
        "review.max_iterations",
        "review.worker_model",
        "review.rubrics.default",
    ):
        assert SelfModRegistry.get_spec(path) is None
