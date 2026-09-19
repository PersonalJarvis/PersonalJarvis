"""Opt-in live-provider proof using a fresh, temporary Jarvis learning workspace.

Run with --provider NAME [--model NAME]. Only synthetic project data is sent.
Credentials resolve through Jarvis; they are never written to the proof artifact.
This measures project-specific planning across a runtime restart, not general intelligence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

# A standalone script must execute this checkout, even when another editable
# install is active in a shared development interpreter.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.agent_chat.surface_kits import kit_for
from jarvis.brain.provider_registry import BrainProviderRegistry
from jarvis.brain.streaming import aggregate
from jarvis.core import runtime_refs
from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig, get_jarvis_agent_secret, override_provider_secrets
from jarvis.core.protocols import BrainMessage, BrainRequest, ChatCompletion, ChatTurn
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import ToolExecutor
from jarvis.society.runtime import SocietyRuntime

QUERY = (
    "Which validation tool must run FIRST for a Project Larch deployment? "
    "Use only this project's established workflow. If no such workflow is known, "
    "return UNKNOWN. Otherwise return only the exact tool name."
)
CORRECTION = (
    "Project Larch workflow correction: the previous deployment skipped validation. "
    "Always run validate_larch_manifest before deploying Project Larch. "
    "Remember this procedure for future deployment sessions."
)


async def verify(provider_name: str, model: str | None) -> dict:
    secret = get_jarvis_agent_secret(provider_name)
    if not secret:
        raise RuntimeError("No configured credential for the selected provider")
    registry = BrainProviderRegistry()
    providers = []

    class Manager:
        def _get_brain(self, name, selected_model=None, *, scope=""):
            if name != provider_name:
                raise RuntimeError("The proof uses exactly one provider family")
            provider = registry.instantiate(name, model=selected_model or model)
            providers.append(provider)
            return provider

    manager = Manager()
    bus = EventBus()
    manager._tool_executor = ToolExecutor(
        bus, RiskTierEvaluator(SafetyConfig()), ApprovalWorkflow(bus)
    )
    previous_manager = runtime_refs.get_brain_manager()
    runtime_refs.set_brain_manager(manager)
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-learning-proof-") as temp:
            root = Path(temp)
            cfg = SimpleNamespace(
                memory=SimpleNamespace(data_dir=str(root)),
                wiki=SimpleNamespace(vault_root=str(root / "vault")),
            )

            async def answer(context):
                provider = manager._get_brain(provider_name, model)
                with override_provider_secrets({provider_name: secret}):
                    response = await aggregate(
                        provider.complete(
                            BrainRequest(
                                system="Answer from the supplied project evidence.\n" + context,
                                messages=(BrainMessage(role="user", content=QUERY),),
                                max_tokens=512,
                                temperature=0,
                            )
                        )
                    )
                return response.text.strip()

            before = await answer("")
            rt = SocietyRuntime(root, cfg=lambda: cfg)
            review_result = None

            async def observed_review(runtime, agent, prompt):
                from jarvis.society.review import _ask

                nonlocal review_result
                review_result = await _ask(runtime, agent, prompt)
                return review_result

            rt.turn_reviewer = observed_review
            await rt.ensure_started()
            try:
                await rt.roster.create(
                    name="Larch",
                    title="Deployment agent",
                    provider=provider_name,
                    model=model or "",
                )
                session = SimpleNamespace(session_id="society:larch", surface="society")
                events = [
                    {"seq": 1, "kind": "user_message", "payload": {"text": CORRECTION}},
                    {"seq": 2, "kind": "turn_finished", "payload": {"status": "done"}},
                ]
                turn = ChatTurn(session.session_id, "correction", CORRECTION, True, "proof")
                await rt.turn_completed(session, ChatCompletion(turn, json.dumps(events)))
                await rt.recover_reviews()
                if rt.conversations.pending_reviews():
                    raise RuntimeError("Live review did not finish; proof is incomplete")
            finally:
                await rt.close()
            rt = SocietyRuntime(root, cfg=lambda: cfg)
            await rt.ensure_started()
            try:
                context = await kit_for("society").session_system_extra(cfg, manager, session)
                after = await answer(context)
                isolated = await answer(await rt.learning_context("jarvis", query=QUERY))
                passed = (
                    before == "UNKNOWN"
                    and after == "validate_larch_manifest"
                    and isolated == "UNKNOWN"
                )
                report = {
                    "passed": passed,
                    "provider": provider_name,
                    "model": model or "default",
                    "before": before,
                    "after_restart": after,
                    "other_agent": isolated,
                    "lessons": len(rt.experience_for("larch").read()["lessons"]),
                    "pending_reviews": len(rt.conversations.pending_reviews()),
                }
                if not passed:
                    report["synthetic_review"] = review_result
                return report
            finally:
                await rt.close()
    finally:
        runtime_refs.set_brain_manager(previous_manager)
        # The SDK clients are private to this proof, never the running app's clients.
        for provider in providers:
            client = getattr(provider, "_client", None)
            close = getattr(client, "close", None)
            if callable(close):
                result = close()
                if hasattr(result, "__await__"):
                    await result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model")
    args = parser.parse_args()
    try:
        result = asyncio.run(verify(args.provider, args.model))
    except Exception as exc:
        # Provider bodies may contain credentials; report the class only.
        frames = traceback.extract_tb(exc.__traceback__)[-3:]
        print(
            json.dumps(
                {
                    "passed": False,
                    "error_type": type(exc).__name__,
                    "frames": [f"{Path(f.filename).name}:{f.lineno}" for f in frames],
                }
            )
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
