"""Regression: a smalltalk / greeting HEAD must not demote a turn that carries a
real action request to a tool-less smalltalk turn.

Live incident 2026-06-19 11:43 (voice session, ``data/jarvis_desktop.log``):
the already-answered "Was geht ab?" turn was recombined onto a brand-new action
request ("... ich möchte, dass du mir den ältesten Post von Bill Gates
aufmachst"). The merged text led with the smalltalk phrase "Was geht ab?", so
``BrainManager._is_smalltalk`` classified the WHOLE turn as smalltalk →
``computer_use`` / ``spawn_worker`` were hidden ("Smalltalk-Turn → nur read-only
Tools fuer LLM sichtbar") → the deep brain produced the no-op "Notiert, deine
Recherche auf X ist gespeichert." and never opened the browser.

The greeting-prefix guard (2026-06-07, "Hallo, öffne ihn für mich") only strips
canonical greetings, not smalltalk-question heads — this test pins that gap and
guards the pure-smalltalk cases against over-reach.
"""
from __future__ import annotations

from jarvis.brain.manager import BrainManager
from jarvis.core.config import JarvisConfig


def _mgr(*, extra_smalltalk: list[str] | None = None) -> BrainManager:
    """A BrainManager stub that only carries what ``_is_smalltalk`` reads."""
    m = BrainManager.__new__(BrainManager)  # bypass heavy __init__
    cfg = JarvisConfig()
    if extra_smalltalk:
        cfg.brain.routing.smalltalk_allowlist = [
            *cfg.brain.routing.smalltalk_allowlist,
            *extra_smalltalk,
        ]
    m._config = cfg
    m._routing_patterns = None
    return m


# The live jarvis.toml augments the allowlist with these (2026-05-01 fix for
# the "es geht ab" → hallucinated spawn). Mirror it so the test reproduces the
# real config the incident ran under.
_LIVE_EXTRA = ["was geht", "es geht ab", "geht ab"]

_BILL_GATES = (
    "Was geht ab? Sehr gut, dass du nachfragst. Ich bin gerade dabei zu "
    "recherchieren auf X, also ehemals Twitter, und ich möchte, dass du mir "
    "dabei hilfst zu recherchieren über den Chrome-Browser und ich möchte, "
    "dass du mir den ältesten Post von Bill Gates aufmachst."
)


# --- the failure: smalltalk head + action tail must classify as a command ---


def test_live_bill_gates_recombine_is_not_smalltalk() -> None:
    m = _mgr(extra_smalltalk=_LIVE_EXTRA)
    assert m._is_smalltalk(_BILL_GATES) is False


def test_wie_gehts_head_with_open_action_is_not_smalltalk() -> None:
    # "wie geht" is in the DEFAULT allowlist — no override needed.
    m = _mgr()
    text = "Wie geht's? Öffne mir bitte Chrome und geh auf x.com."
    assert m._is_smalltalk(text) is False


def test_greeting_then_smalltalk_then_action_is_not_smalltalk() -> None:
    m = _mgr(extra_smalltalk=_LIVE_EXTRA)
    text = "Hey, was geht ab? Mach mir mal bitte den ältesten Bill-Gates-Post auf."
    assert m._is_smalltalk(text) is False


# --- pure smalltalk must STAY smalltalk (no over-reach) ---------------------


def test_pure_was_geht_ab_stays_smalltalk() -> None:
    m = _mgr(extra_smalltalk=_LIVE_EXTRA)
    assert m._is_smalltalk("Was geht ab?") is True


def test_pure_wie_gehts_stays_smalltalk() -> None:
    m = _mgr()
    assert m._is_smalltalk("Wie geht's?") is True


def test_greeting_plus_smalltalk_stays_smalltalk() -> None:
    m = _mgr()
    assert m._is_smalltalk("Hallo, wie geht's dir heute?") is True


def test_long_friendly_smalltalk_without_request_stays_smalltalk() -> None:
    # A long, warm chit-chat with a "wie geht" head but NO action/request
    # signal must stay smalltalk — the anti-fake-spawn tool-hiding relies on it.
    m = _mgr()
    text = "Wie geht's dir denn so an diesem wunderschönen Morgen, mein lieber Freund?"
    assert m._is_smalltalk(text) is True


def test_plain_non_smalltalk_action_is_not_smalltalk() -> None:
    # No smalltalk head at all — must remain a command (unchanged behaviour).
    m = _mgr()
    assert m._is_smalltalk("Öffne mir Chrome und geh auf x.com.") is False
