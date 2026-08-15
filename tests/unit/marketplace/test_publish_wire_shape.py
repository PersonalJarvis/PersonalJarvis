"""The publish wire model must carry every field the validator reads.

`SubmissionDraft` is a pydantic model with the default `extra="ignore"`, so a
key the form sends but the model does not declare is dropped *silently* — no
error, no warning. The submission then travels on without that piece and the
author is told it succeeded.

That is not hypothetical: the Publish tab sent bundled `skills`, the model
discarded them, and `validate_draft`'s whole bundled-skill rule set ran against
`None` on every real request. A package published with skills arrived in the
store without them.

These tests pin the invariant structurally, so the next field added to the form
cannot repeat it.
"""

from __future__ import annotations

import inspect
import re

from jarvis.marketplace import publish
from jarvis.ui.web.marketplace_publish_routes import SubmissionDraft

# Keys `validate_draft` (and the helpers it calls in the same module) read off
# the incoming draft dict.
_DRAFT_GET_RE = re.compile(r"draft\.get\(\s*[\"']([a-z_]+)[\"']")


def _keys_the_validator_reads() -> set[str]:
    source = inspect.getsource(publish.validate_draft)
    return set(_DRAFT_GET_RE.findall(source))


def test_wire_model_declares_every_field_the_validator_reads() -> None:
    """A key the validator reads but the model omits is silently dropped.

    The failure mode is a success message over an incomplete package, which is
    worse than a rejection: the author has no reason to look.
    """
    declared = set(SubmissionDraft.model_fields)
    read = _keys_the_validator_reads()
    missing = read - declared
    assert not missing, (
        f"validate_draft reads {sorted(missing)}, but SubmissionDraft does not "
        f"declare them — pydantic drops undeclared keys silently, so these "
        f"never reach the validator. Declare them on the model."
    )


def test_bundled_skills_survive_the_wire_model() -> None:
    """The regression itself: skills must reach `validate_draft` intact."""
    draft = SubmissionDraft(
        kind="plugin",
        name="todo-fox",
        version="1.0.0",
        skills=[{"name": "three-bullet-brief", "skill_md": "---\nname: x\n---\n"}],
    )
    dumped = draft.model_dump()
    assert dumped["skills"] == [
        {"name": "three-bullet-brief", "skill_md": "---\nname: x\n---\n"}
    ]


def test_a_submission_without_skills_still_dumps_the_key_as_none() -> None:
    """A plain connector keeps working — `None`, not a missing key, so the
    validator's `draft.get("skills")` branch reads the same either way."""
    draft = SubmissionDraft(kind="plugin", name="todo-fox", version="1.0.0")
    assert draft.model_dump()["skills"] is None
