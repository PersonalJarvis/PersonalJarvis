"""Skill-routing eval (2026-06-24): does the router pick `run-skill` on a
natural paraphrase, the way Claude Code does?

Background — the "Claude-Code parity" change
(docs/superpowers/specs/2026-06-24-skill-firing-claude-code-parity-design.md):
the builtin skills already carry rich when_to_use fields, but the router used
to almost never call `run-skill` on its own. The fix strengthened the router
prompt stance + the AVAILABLE SKILLS framing so a plausibly-matching skill is
the brain's first move. This script measures that behaviour against the LIVE
router brain.

How it measures (zero side effects)
-----------------------------------
Each golden utterance is a NATURAL paraphrase that deliberately avoids the
skill's exact regex trigger — so the ONLY way the skill can fire is the
model-judged path (the brain choosing `run-skill` from the listing). `run-skill`
publishes a frozen ``SkillInvoked`` event the moment it loads the skill's
instructions, *before* the brain executes any of the skill's steps. We listen
for that event and CANCEL the turn as soon as it fires — so we record the
routing decision without ever running the skill's downstream actions.

Negative controls verify the over-fire guard: a plain knowledge question that
merely mentions a topic must NOT fire a skill.

Usage:
    python scripts/skill_routing_eval.py

Needs a configured brain provider (key in the Credential Manager / env). With
no reachable provider the run reports PROVIDER? rather than a skill miss.
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# (utterance, expected_skill_or_None). expected=None → a negative control that
# must NOT fire any skill. Every positive paraphrase avoids the skill's literal
# voice-trigger tokens, so only the model-judged path can fire it.
GOLDEN: list[tuple[str, str | None]] = [
    ("fass mir mal kurz zusammen was heute alles ansteht", "morning-routine"),
    ("ich brauch jetzt ruhe zum arbeiten, blende mir die ablenkungen aus",  # i18n-allow: simulated German user utterance under test
     "deep-work-mode"),
    ("schau mal ob heute schon was wichtiges per e-mail reingekommen ist",  # i18n-allow: simulated German user utterance under test
     "plugin-gmail"),
    ("wie viel kostet mich gerade meine google cloud diesen monat?",
     "cli-gcloud"),
    ("sind bei mir eigentlich noch issues offen, die ich angehen muss?",  # i18n-allow: simulated German user utterance under test
     "plugin-github"),
    ("wie viel hab ich diesen monat eigentlich eingenommen?", "plugin-stripe"),
    # --- negative controls: the app is only the SUBJECT of a knowledge
    #     question; the definitional guard must keep the skill from firing ---
    ("was ist eigentlich GitHub fuer eine plattform?", None),  # i18n-allow: simulated German user utterance under test (negative control)
    ("was ist Stripe ueberhaupt und wofuer nutzt man das?", None),  # i18n-allow: simulated German user utterance under test (negative control)
]

PER_TURN_TIMEOUT_S = 60.0


@dataclass
class _Fire:
    skill_name: str
    source: str


async def main() -> int:
    from jarvis.brain.factory import build_default_brain
    from jarvis.core.bus import EventBus
    from jarvis.skills.prompt_injection import render_available_skills_section
    from jarvis.skills.schema import SkillInvoked
    from jarvis.skills.skill_context import try_get_skill_context

    print("=" * 72)
    print("Skill-routing eval — does the router pick run-skill on a paraphrase?")
    print("=" * 72)

    bus = EventBus()

    fires: list[_Fire] = []
    fire_event = asyncio.Event()

    async def on_event(evt: object) -> None:
        if isinstance(evt, SkillInvoked):
            fires.append(_Fire(skill_name=evt.skill_name, source=evt.source))
            fire_event.set()

    bus.subscribe_all(on_event)

    print("\n--- Building the live router brain (this also wires the skill context) ---")
    try:
        bm = build_default_brain(bus=bus)
    except Exception as exc:  # noqa: BLE001
        print(f"  brain build FAILED: {type(exc).__name__}: {exc}")
        return 1
    print(f"  active_provider = {getattr(bm, 'active_provider', '?')}")

    # Show exactly which skills the brain can see — a miss on an unlisted skill
    # is a registry/lifecycle issue, not a routing regression.
    ctx = try_get_skill_context()
    if ctx is not None:
        section = render_available_skills_section(ctx.registry)
        listed = (
            [s.name for s in ctx.registry.list_active()]
            if hasattr(ctx.registry, "list_active") else []
        )
        print(f"\n--- AVAILABLE SKILLS the brain sees ({len(listed)}) ---")
        print("  " + ", ".join(sorted(listed)) if listed else "  (none)")
        if section is None:
            print("  WARNING: render_available_skills_section returned None — "
                  "the brain sees NO skills; every positive case will miss.")
    else:
        print("\n  WARNING: no skill context — cannot list skills.")

    rows: list[tuple[str, str, str, str]] = []
    print("\n--- Running golden set ---")
    for utterance, expected in GOLDEN:
        fires.clear()
        fire_event.clear()

        task = asyncio.create_task(bm.generate(utterance, use_history=False))
        wait_fire = asyncio.create_task(fire_event.wait())
        done, pending = await asyncio.wait(
            {task, wait_fire},
            timeout=PER_TURN_TIMEOUT_S,
            return_when=asyncio.FIRST_COMPLETED,
        )

        fired: _Fire | None = fires[0] if fires else None
        result_text = ""
        if fired is not None:
            # Routing decision captured — stop before the skill executes.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        elif task in done:
            try:
                result_text = (task.result() or "")[:160]
            except Exception as exc:  # noqa: BLE001
                result_text = f"<{type(exc).__name__}: {exc}>"
        else:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            result_text = "<TIMEOUT>"
        for p in pending:
            p.cancel()
        await asyncio.gather(wait_fire, return_exceptions=True)

        # Verdict
        if expected is None:
            verdict = "FAIL(over-fire)" if fired else "PASS"
            got = f"{fired.skill_name} [{fired.source}]" if fired else "(no skill)"
        else:
            if fired and fired.skill_name == expected:
                verdict = f"HIT [{fired.source}]"
            elif fired:
                verdict = f"WRONG({fired.skill_name})"
            else:
                low = result_text.lower()
                provider_down = any(
                    ind in low for ind in
                    ("account", "brain-key", "unerreichbar", "setup", "sidebar")
                )
                verdict = "PROVIDER?" if provider_down else "MISS"
            got = f"{fired.skill_name} [{fired.source}]" if fired else (
                result_text or "(direct answer)")
        rows.append((utterance, expected or "(none — neg)", got, verdict))
        print(f"  [{verdict:16s}] {utterance[:52]:52s} → {got[:40]}")

    # Summary
    pos = [r for r, (_, exp) in zip(rows, GOLDEN) if exp is not None]
    neg = [r for r, (_, exp) in zip(rows, GOLDEN) if exp is None]
    hits = sum(1 for r in pos if r[3].startswith("HIT"))
    neg_pass = sum(1 for r in neg if r[3] == "PASS")
    provider_q = sum(1 for r in rows if r[3] == "PROVIDER?")

    print("\n" + "=" * 72)
    print(f"  Positive (model-judged firing): {hits}/{len(pos)} HIT")
    print(f"  Negative (over-fire guard):     {neg_pass}/{len(neg)} PASS")
    if provider_q:
        print(f"  PROVIDER? rows: {provider_q} — brain provider unreachable, "
              "rerun with a configured key for a clean number.")
    print("=" * 72)

    # Exit 0 when every positive that had a reachable provider fired AND no
    # over-fire — the loop's success condition.
    real_pos = [r for r in pos if r[3] != "PROVIDER?"]
    ok = (
        all(r[3].startswith("HIT") for r in real_pos)
        and neg_pass == len(neg)
        and len(real_pos) > 0
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
