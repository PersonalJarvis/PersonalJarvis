"""Keep every unit test away from the developer's real polish credentials.

``[dictation].polish`` ships ON, and the pass resolves its provider chain from
whatever keys the HOST holds. That combination is fine in production and
poisonous in a test suite: the moment the dictation delivery path grew a polish
hook, every test that finishes a dictation with a default ``DictationConfig``
started making a live model call on a machine that happens to have a Groq or
Gemini key — billing the developer, taking up to the full latency ceiling per
test, and returning text that is legitimately different every run. On CI, which
holds no keys at all, the same tests take the empty-chain path and pass
instantly. A suite whose behaviour depends on who is running it is the AP-23
inversion in miniature.

So the CONSUMER's view of the chain is emptied for every unit test: the pass
resolves no family, reports ``unavailable``, and hands back the raw transcript —
byte-identical to the behaviour before the feature existed, which is exactly
what the vast majority of these tests are asserting about.

Two things this deliberately does NOT do:

* It does not touch ``jarvis.dictation.polish_client.resolve_polish_chain``.
  That function is itself under test (key-aware ordering, one entry per family,
  the pin), and neutralising it would make those tests assert against this
  fixture instead of against the code.
* It does not stop a test from polishing. A test that wants the pass to run
  patches the same attribute in its own body, which happens after this fixture
  and therefore wins — the pattern the polish-pipeline tests already use.

This lives at the ``tests/unit`` level rather than in the dictation folder on
purpose: the delivery path is reached from the speech suite and from the REST
route suite as well, and a guarantee that only covers the tests somebody
remembered is not a guarantee (the same reasoning as the root conftest's
history redirect).
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _polish_pass_holds_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the polish pass see an empty provider chain, as a keyless host does."""
    from jarvis.dictation import polish

    monkeypatch.setattr(polish, "resolve_polish_chain", lambda cfg: ())
