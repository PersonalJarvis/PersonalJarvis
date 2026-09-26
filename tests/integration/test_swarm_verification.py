"""Real sandbox acceptance rejects fabricated evidence and preserves receipts."""

import json
from decimal import Decimal

import pytest

from jarvis.brain.swarm_factory import SwarmProvider
from jarvis.control.cancel import CancelToken
from jarvis.core.protocols import BrainDelta
from jarvis.core.swarm_types import TaskSpec
from jarvis.swarm.receipts import is_runtime_receipt
from jarvis.swarm.sandbox import WasmSandbox
from jarvis.swarm.tools import SwarmToolkit
from tests.fakes.swarm_runtime import runtime
from tests.fakes.swarm_storage import running_team


@pytest.mark.asyncio
@pytest.mark.parametrize("proof_kind", ["none", "forged", "unrelated", "late", "real"])
async def test_only_current_runtime_execution_can_pass_original_verifier(tmp_path, proof_kind):
    task = TaskSpec(
        id="work",
        title="Arithmetic",
        description="Compute seven squared",
        acceptance="Return the numeric value 49 after executing the calculation",
        verification="javascript",
        verification_script="function main(input) { return {accepted: input.result === 49}; }",
    )
    fixture = running_team(tmp_path / "records", tasks=[task])
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    service = runtime(tmp_path / "runtime", sandbox=WasmSandbox())
    cancel = CancelToken()
    toolkit = SwarmToolkit(service, fixture.store, actor, fixture.controller, cancel)
    evidence = []
    if proof_kind == "late":
        for index in range(30):
            evidence.append(
                fixture.store.write_artifact(
                    actor, f"scratch-{index}.txt", "Scratch work", f"scratch-{index}"
                )["id"]
            )
    if proof_kind == "forged":
        record = fixture.store.write_artifact(
            actor,
            "execution.json",
            json.dumps({"provenance": "runtime-receipt", "execution": {"output": 49}}),
            "forged",
        )
        evidence.append(record["id"])
    if proof_kind in {"real", "late", "unrelated"}:
        computed = await toolkit.execute(
            "run_javascript",
            {
                "script": "function main(input) { return input.n ** 2; }",
                "inputs": {"n": 7 if proof_kind in {"real", "late"} else 1},
            },
            "compute",
        )
        assert computed.success, computed.error
        evidence.extend(toolkit.evidence)
    providers = []
    try:
        verdict = await service._verify(
            fixture.store,
            fixture.controller,
            actor,
            fixture.store.read_task(actor, actor.task_id),
            "49",
            evidence,
            cancel,
            providers,
        )
        assert verdict["accepted"] is (proof_kind in {"real", "late"})
        metadata, content = fixture.store.download_artifact(evidence[-1])
        assert is_runtime_receipt(metadata, "verification", actor)
        receipt = json.loads(content)
        assert receipt["payload"]["accepted"] is verdict["accepted"]
        assert receipt["subject_ids"] == evidence[:-1]
        assert providers == []
        assert len(evidence) <= 32
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_review_shares_duplicate_bytes_but_preserves_every_authentic_artifact(tmp_path):
    original = TaskSpec(
        id="work",
        title="Source comparison",
        description="Compare the captured source against its changed edition",
        acceptance="Keep the exact source attribution and describe the edition difference",
        required_tools=["fetch_url"],
    )
    fixture = running_team(tmp_path / "records", tasks=[original])
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    service = runtime(tmp_path / "runtime")
    captured = []

    class Reviewer:
        async def complete(self, request):
            captured.append(request)
            yield BrainDelta(
                content='{"accepted":true,"reason":"Captured review fixture"}',
                finish_reason="stop",
                usage={"input_tokens": 100, "output_tokens": 10},
            )

    class Factory:
        async def create(self):
            return SwarmProvider(Reviewer(), "test-api", "test-model", Decimal("1"))

        def failed(self, provider):
            pytest.fail("This fixture must not fail provider inference")

    service.brain_factory = Factory()
    source = "SOURCE-A " + "x" * 39_000
    first = fixture.store.write_artifact(actor, "first.txt", source, "first-body")
    second = fixture.store.write_artifact(actor, "second.txt", source, "second-body")
    changed = fixture.store.write_artifact(
        actor, "changed.txt", source + " edition B", "changed-body"
    )
    payload = {
        "url": "https://example.org/source",
        "status": 200,
        "body_sha256": first["sha256"],
    }
    receipts = [
        fixture.store.capture_receipt(
            fixture.controller, actor, "http", payload, [first["id"], second["id"]], key
        )
        for key in ("source-proof-one", "source-proof-two")
    ]
    assert receipts[0]["id"] != receipts[1]["id"]
    assert receipts[0]["sha256"] == receipts[1]["sha256"]
    records = [first, second, changed, *receipts]
    evidence = [record["id"] for record in records]
    task = fixture.store.read_task(actor, "work")
    result = "The later source adds edition B; both original captures are attributed."
    providers = []
    try:
        verdict = await service._verify(
            fixture.store,
            fixture.controller,
            actor,
            task,
            result,
            evidence,
            CancelToken(),
            providers,
        )
        assert verdict["accepted"]
        request = captured[0]
        prompt = json.loads(request.messages[0].content)
        assert prompt["original_task"] == task
        assert prompt["proposed_result"] == result
        assert "Resolve only its content" in request.system
        projected = {item["id"]: item for item in prompt["artifacts"]}
        assert set(projected) == {record["id"] for record in records}
        for record in records:
            assert all(projected[record["id"]][key] == value for key, value in record.items())
            assert projected[record["id"]]["source_verified"] is False
        for pair in ((first, second), receipts):
            members = [projected[record["id"]] for record in pair]
            original_content = next(item for item in members if "content" in item)
            reference = next(item for item in members if "content_ref" in item)
            assert reference["content_ref"] == original_content["id"]
            assert sum("content" in item for item in members) == 1
        for receipt in receipts:
            assert projected[receipt["id"]]["verified_runtime_kind"] == "http"
            assert projected[receipt["id"]]["current_attempt"] is True
        assert projected[changed["id"]]["content"] == source + " edition B"
        assert "content_ref" not in projected[changed["id"]]
        assert request.messages[0].content.count(json.dumps(source)) == 1
        # The verifier receipt still binds every distinct source and proof identity.
        _, proof = fixture.store.download_artifact(evidence[-1])
        assert set(json.loads(proof)["subject_ids"]) == {record["id"] for record in records}
    finally:
        for provider in providers:
            await provider.aclose()
        await service.stop()
