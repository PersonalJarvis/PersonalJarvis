"""Can this skill ever be FOUND? — separate from "does it load?".

``validator.py`` answers whether a SKILL.md parses and is safe. This module
answers a different question that nothing asked before: whether a skill has
enough distinctive vocabulary to be reachable at all. A skill can be perfectly
valid, perfectly enabled, and still invisible to every matching channel.

That is not hypothetical. Live on the maintainer's disk:

* ``browser-tabs`` — ``description: ''``, a body of one heading, and ``name:
  Browser Tabs`` (a display string, not a slug, which also breaks by-name
  ``run-skill`` lookup). It cannot be matched, listed usefully, or invoked.
* ``control-api`` — no trigger and English-only vocabulary, so a German request
  loses to whichever plugin's product noun happens to collide. The routing eval
  records it as a known gap.

**Deliberately NOT merged into ``validate_skill``.** Merging would demote
several of the maintainer's existing skills to DRAFT on the next hot reload — a
self-inflicted outage delivered as a safety improvement.

**And deliberately not enforced by the loader.** Enforce at the WRITE boundary,
report at the READ boundary, never at the LOAD boundary. A content rule applied
at load time suppresses the skills nobody anticipated as well as the bad ones,
silently, at boot, with no signal — which is AP-27's shape.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["error", "warning"]

# --- codes ------------------------------------------------------------------

Q_EMPTY_DESCRIPTION = "Q001"
Q_NO_WHEN_TO_USE = "Q002"
Q_EMPTY_BODY = "Q003"
Q_TOO_FEW_TERMS = "Q004"
Q_TERM_COLLISION = "Q005"
Q_GREEDY_TRIGGER = "Q006"
Q_NAME_NOT_A_SLUG = "Q007"
Q_MISSION_WITHOUT_WHEN = "Q008"

QUALITY_CODES: tuple[str, ...] = (
    Q_EMPTY_DESCRIPTION,
    Q_NO_WHEN_TO_USE,
    Q_EMPTY_BODY,
    Q_TOO_FEW_TERMS,
    Q_TERM_COLLISION,
    Q_GREEDY_TRIGGER,
    Q_NAME_NOT_A_SLUG,
    Q_MISSION_WITHOUT_WHEN,
)

#: Below this many distinctive terms a skill is unreachable by construction —
#: no channel has anything to match on.
MIN_DISTINCTIVE_TERMS = 3

#: Vocabulary overlap above this against another active skill means the two are
#: competing for the same requests and one will always lose.
COLLISION_THRESHOLD = 0.6

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(frozen=True, slots=True)
class QualityFinding:
    code: str
    severity: Severity
    message: str
    hint: str = ""


@dataclass(frozen=True, slots=True)
class QualityReport:
    skill_name: str = ""
    findings: tuple[QualityFinding, ...] = field(default_factory=tuple)

    @property
    def errors(self) -> tuple[QualityFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "error")

    @property
    def warnings(self) -> tuple[QualityFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "warning")

    @property
    def ok(self) -> bool:
        """True when nothing blocks. Warnings do not block."""
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "ok": self.ok,
            "findings": [
                {
                    "code": f.code,
                    "severity": f.severity,
                    "message": f.message,
                    "hint": f.hint or None,
                }
                for f in self.findings
            ],
        }


def distinctive_terms(skill: Any) -> frozenset[str]:
    """Content terms this skill can be matched on.

    Uses the shared scorer's own surfaces and stopword handling, so "what can be
    matched" here means exactly what the matcher can match — not a second
    opinion that drifts from it.
    """
    try:
        from jarvis.skills.relevance import _skill_surfaces, tokenize
    except Exception:  # noqa: BLE001
        return frozenset()
    terms: set[str] = set()
    try:
        for _field, text in _skill_surfaces(skill):
            terms |= set(tokenize(text))
    except Exception:  # noqa: BLE001
        return frozenset()
    return frozenset(terms)


def _text(value: Any) -> str:
    return str(value or "").strip()


def lint_skill(skill: Any, *, peers: list[Any] | None = None) -> QualityReport:
    """Report whether ``skill`` can be found. Never raises.

    ``peers`` are the other active skills, used for the collision check. Omit
    them to skip that check (e.g. when linting a draft before it is installed).
    """
    name = str(getattr(skill, "name", "") or "")
    findings: list[QualityFinding] = []
    frontmatter = getattr(skill, "frontmatter", None)

    if frontmatter is None:
        # A parse failure is validator territory; there is nothing to judge here.
        return QualityReport(skill_name=name, findings=())

    # Q007 — a display-string name is a functional break, not a style nit: the
    # brain looks skills up by name, so "Browser Tabs" cannot be invoked.
    if name and not _SLUG_RE.match(name):
        findings.append(
            QualityFinding(
                code=Q_NAME_NOT_A_SLUG,
                severity="warning",
                message=f"name {name!r} is a display string, not a slug",
                hint="use lowercase-with-hyphens; run-skill looks skills up by name",
            )
        )

    description = _text(getattr(frontmatter, "description", ""))
    when_to_use = _text(getattr(frontmatter, "when_to_use", ""))

    if not description:
        findings.append(
            QualityFinding(
                code=Q_EMPTY_DESCRIPTION,
                severity="error",
                message="description is empty",
                hint=(
                    "the description IS the listing entry the model reads — "
                    "without it the skill is invisible to every channel"
                ),
            )
        )
    if not when_to_use:
        findings.append(
            QualityFinding(
                code=Q_NO_WHEN_TO_USE,
                severity="warning",
                message="no when_to_use",
                hint=(
                    "quote the phrases a user would actually say, in the "
                    "languages they speak — that text is what the matcher mines"
                ),
            )
        )

    if not _body_has_instructions(skill):
        findings.append(
            QualityFinding(
                code=Q_EMPTY_BODY,
                severity="error",
                message="body contains no instructions",
                hint="a skill whose body is only a heading does nothing when it fires",
            )
        )

    terms = distinctive_terms(skill)
    if len(terms) < MIN_DISTINCTIVE_TERMS:
        findings.append(
            QualityFinding(
                code=Q_TOO_FEW_TERMS,
                severity="error",
                message=(
                    f"only {len(terms)} distinctive term(s) — unreachable by "
                    "construction"
                ),
                hint=(
                    "add tags, a when_to_use, or intent_objects; function words "
                    "do not count"
                ),
            )
        )

    execution = str(getattr(frontmatter, "execution", "inline") or "inline").lower()
    if execution == "mission" and not when_to_use:
        findings.append(
            QualityFinding(
                code=Q_MISSION_WITHOUT_WHEN,
                severity="error",
                message="execution: mission without when_to_use",
                hint=(
                    "a mission skill can never auto-fire (it starts a process), "
                    "so the model must be able to choose it — and it chooses "
                    "from when_to_use"
                ),
            )
        )

    triggers = tuple(getattr(frontmatter, "triggers", ()) or ())
    for trigger in triggers:
        if getattr(trigger, "type", "") != "voice":
            continue
        pattern = _text(getattr(trigger, "pattern", ""))
        if not pattern:
            continue
        core = pattern.strip("^$()")
        if core and "|" not in core and len(core) < 4 and not core.startswith("\\"):
            findings.append(
                QualityFinding(
                    code=Q_GREEDY_TRIGGER,
                    severity="warning",
                    message=f"trigger {pattern!r} is a very short bare pattern",
                    hint="it will match unrelated turns; anchor it or add context",
                )
            )

    if peers and terms:
        for peer in peers:
            peer_name = str(getattr(peer, "name", "") or "")
            if not peer_name or peer_name == name:
                continue
            peer_terms = distinctive_terms(peer)
            if not peer_terms:
                continue
            overlap = len(terms & peer_terms) / max(1, len(terms | peer_terms))
            if overlap > COLLISION_THRESHOLD:
                findings.append(
                    QualityFinding(
                        code=Q_TERM_COLLISION,
                        severity="warning",
                        message=(
                            f"vocabulary is {overlap:.0%} identical to "
                            f"{peer_name!r}"
                        ),
                        hint=(
                            "the two compete for the same requests and one will "
                            "always lose — give each a distinctive term"
                        ),
                    )
                )
                break

    return QualityReport(skill_name=name, findings=tuple(findings))


def _body_has_instructions(skill: Any) -> bool:
    """Reuse the authoring guard so both paths agree on "empty body"."""
    body = str(getattr(skill, "body", "") or "")
    try:
        from jarvis.skills.authoring.service import body_has_instructions

        return bool(body_has_instructions(body))
    except Exception:  # noqa: BLE001
        # Conservative local fallback: at least one non-heading, non-blank line.
        for line in body.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return True
        return False


def lint_registry(skills: list[Any]) -> list[QualityReport]:
    """Lint every skill against its peers. Used by the health strip."""
    active = list(skills or ())
    return [lint_skill(skill, peers=active) for skill in active]


__all__ = [
    "COLLISION_THRESHOLD",
    "MIN_DISTINCTIVE_TERMS",
    "QUALITY_CODES",
    "Q_EMPTY_BODY",
    "Q_EMPTY_DESCRIPTION",
    "Q_GREEDY_TRIGGER",
    "Q_MISSION_WITHOUT_WHEN",
    "Q_NAME_NOT_A_SLUG",
    "Q_NO_WHEN_TO_USE",
    "Q_TERM_COLLISION",
    "Q_TOO_FEW_TERMS",
    "QualityFinding",
    "QualityReport",
    "Severity",
    "distinctive_terms",
    "lint_registry",
    "lint_skill",
]
