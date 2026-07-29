"""Import-graph guards for the polish pass — AP-26 (boot) and AP-11 (voice).

Two anti-patterns meet in this feature, and both of them are invisible in a
normal test run because a test process has already imported half the world.

**AP-26 — nothing initialises before the app is interactive.** The polish pass
carries an HTTP stack and, on one transport, a Google SDK. If the speech
pipeline ever grew a module-scope ``from jarvis.dictation.polish import ...``,
that weight would move onto the boot path, where the budget gate measures a
window of 8 seconds. The hook belongs INSIDE ``_finish_dictation``, exactly
where ``clean_transcript`` already is.

**AP-11 — no LLM call inside the voice scrubber.** ``scrub_for_voice`` is regex
only, on the hot path between the brain and TTS. The polish pass is a
model call; it lives in the dictation lane and must never acquire a call site
in ``jarvis/brain/``.

Both are measured in a FRESH interpreter, because the only honest way to ask
"does importing X pull in Y" is to import X into a process that has imported
nothing else. The control test proves the probe can see a module when one is
genuinely there, so a broken probe fails loudly instead of going green.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PIPELINE = _REPO_ROOT / "jarvis" / "speech" / "pipeline.py"
_OUTPUT_FILTER = _REPO_ROOT / "jarvis" / "brain" / "output_filter.py"
_BRAIN_DIR = _REPO_ROOT / "jarvis" / "brain"

#: The names whose presence in ``sys.modules`` would mean the polish pass (or
#: the transports it owns) had been dragged along.
_PROBE = """
import json, sys
{imports}
found = sorted(
    name for name in sys.modules
    if name.startswith("jarvis.dictation.polish")
    or name == "httpx"
    or name.startswith("google.genai")
)
print("JARVIS_PROBE " + json.dumps(found))
"""


def _modules_pulled_in_by(*modules: str) -> list[str]:
    """Import *modules* in a fresh interpreter; report which markers appeared."""
    code = _PROBE.format(imports="\n".join(f"import {name}" for name in modules))
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, (
        f"probe import of {modules} failed:\n{proc.stdout}\n{proc.stderr}"
    )
    for line in (proc.stdout or "").splitlines():
        if line.startswith("JARVIS_PROBE "):
            return json.loads(line[len("JARVIS_PROBE ") :])
    raise AssertionError(f"probe produced no verdict line:\n{proc.stdout}")


def test_the_probe_actually_sees_the_polish_modules_when_they_are_imported() -> None:
    """The control. Without this, every assertion below could be passing simply
    because the probe never worked."""
    found = _modules_pulled_in_by("jarvis.dictation.polish")
    assert "jarvis.dictation.polish" in found, found
    assert "jarvis.dictation.polish_client" in found, found
    assert "jarvis.dictation.polish_guards" in found, found
    assert "jarvis.dictation.polish_prompt" in found, found


def test_importing_the_speech_pipeline_does_not_import_the_polish_pass() -> None:
    """AP-26. The hook is a lazy import inside ``_finish_dictation``; the day it
    moves to module scope, the pipeline starts paying for an HTTP stack and a
    model SDK on every boot."""
    found = _modules_pulled_in_by("jarvis.speech.pipeline")
    assert found == [], (
        "importing jarvis.speech.pipeline pulled in "
        f"{found} — the polish pass must stay off the boot path (AP-26)"
    )


def test_importing_the_voice_output_filter_does_not_import_the_polish_pass() -> None:
    """AP-11. ``scrub_for_voice`` is regex-only and sits on the brain->TTS hot
    path; a model call must not be one import away from it."""
    found = _modules_pulled_in_by("jarvis.brain.output_filter")
    assert found == [], (
        "importing jarvis.brain.output_filter pulled in "
        f"{found} — the voice scrubber must never reach the polish pass (AP-11)"
    )


def test_the_voice_output_filter_names_nothing_from_the_polish_pass() -> None:
    """The static half of AP-11: not even a deferred, inside-a-function import."""
    assert _OUTPUT_FILTER.exists(), f"missing: {_OUTPUT_FILTER}"
    source = _OUTPUT_FILTER.read_text(encoding="utf-8")
    assert "dictation.polish" not in source, _OUTPUT_FILTER.name
    assert "polish_transcript" not in source, _OUTPUT_FILTER.name


def test_no_module_under_jarvis_brain_calls_the_polish_pass() -> None:
    """AP-11 across the whole brain layer, not just the one scrubber file.

    The polish pass formats dictation. A call site inside the brain would mean
    it had quietly become part of a spoken turn, which is the latency
    regression AP-11 exists to prevent.
    """
    assert _BRAIN_DIR.is_dir(), f"missing: {_BRAIN_DIR}"
    offenders: list[str] = []
    for path in sorted(_BRAIN_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "dictation.polish" in text or "polish_transcript" in text:
            offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert offenders == [], offenders


def test_any_polish_import_in_the_speech_pipeline_is_inside_a_function() -> None:
    """AP-26, statically. A module-scope import is flush against the left
    margin; the lazy hook that keeps the boot budget intact is indented.

    Passes vacuously while the pipeline hook has not landed yet — which is
    correct: "no import at all" also satisfies "no import at module scope".
    """
    assert _PIPELINE.exists(), f"missing: {_PIPELINE}"
    module_scope: list[str] = []
    for number, line in enumerate(
        _PIPELINE.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        if "jarvis.dictation.polish" not in line:
            continue
        stripped = line.lstrip()
        if not stripped.startswith(("import ", "from ")):
            continue
        if line[: len(line) - len(stripped)] == "":
            module_scope.append(f"{_PIPELINE.name}:{number}: {stripped}")
    assert module_scope == [], module_scope
