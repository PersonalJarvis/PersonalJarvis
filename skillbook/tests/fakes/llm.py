"""FakeLLM: deterministic hand-written test double for the LLM Protocol.

Lives in tests/ — not production. Production code that wants an offline default
must construct this explicitly via ``from tests.fakes.llm import FakeLLM``.
Per ADR-0010 the previous ``src/skillbook/ace_core/llm.py:MockLLM`` was moved
here and renamed; the canned analysis source it emits is unchanged so the
capstone scenario continues to converge to the same Verdict.
"""

from __future__ import annotations

_REFLECTOR_PROMPT_MARKER = "SKB_TRACE_PATH"


_REFLECTOR_ANALYSIS_CODE = '''\
import json, os, sys

trace_path = os.environ["SKB_TRACE_PATH"]
with open(trace_path, "r", encoding="utf-8") as fh:
    trace = json.load(fh)

failures = [s for s in trace if s.get("status") in ("TIMEOUT", "BLOCKED_BY_GUARDRAIL")]
if not failures:
    print(json.dumps({"outcome": "no_action", "evidence": "no failure observed in trace", "rule": None}))
    sys.exit(0)

# Aggregate the failing actor and the most-common diagnostic failure-mode.
last = failures[-1]
actor = last.get("actor", "<unknown>")

# The diagnostic was serialized into the result dict by the Generator when the
# step was BLOCKED_BY_GUARDRAIL; pull failure_mode if present.
failure_mode = None
result = last.get("result") or {}
if isinstance(result, dict):
    failure_mode = result.get("failure_mode")

evidence = (
    "Actor " + repr(actor) + " observed failing " + str(len(failures)) +
    " time(s); failure_mode=" + repr(failure_mode)
)

verdict = {
    "outcome": "failure",
    "evidence": evidence,
    "rule": {
        "trigger": {"actor": actor},
        "strategy": {
            "kind": "retry_with_delay",
            "delay_s": 3,
            "max_retries": 2,
        },
    },
}
print(json.dumps(verdict))
'''


class FakeLLM:
    """Deterministic stand-in satisfying the :class:`skillbook.ace_core.llm.LLM` Protocol.

    Recognizes prompts that mention the reflector's trace-path marker and
    returns Python source that reads the trace JSON, finds failing steps,
    and writes a Verdict on stdout. Any other prompt yields a code-shaped
    program that prints a ``no_action`` verdict.

    Renamed from the previous in-tree ``MockLLM`` because Gerard Meszaros'
    xUnit Patterns vocabulary uses *Fake* for hand-written replacements and
    reserves *Mock* for behavior-verifying frameworks. See ADR-0010.
    """

    async def complete(self, prompt: str, *, max_tokens: int = 800) -> str:
        if _REFLECTOR_PROMPT_MARKER in prompt:
            return _REFLECTOR_ANALYSIS_CODE
        return (
            "import json, sys\n"
            'print(json.dumps({"outcome": "no_action", "evidence": "fake-llm: unrecognized prompt", "rule": None}))\n'
        )
