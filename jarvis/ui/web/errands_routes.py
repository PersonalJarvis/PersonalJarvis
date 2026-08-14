"""REST surface for autonomous errands (docs/plans/autonomous-missions.md).

CLI-first contract (§5): the voice tools in ``jarvis/plugins/tool/`` and this
router are the SAME four verbs — list, inspect, answer, cancel — so the CLI
(auto-generated from OpenAPI) and any UI panel see exactly what the voice path
sees. Reads work even before the brain stack is up (they only need the store);
answer/cancel need the live runner and answer 503 honestly when it is absent,
never a fake success.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from jarvis.errands.schema import Errand
from jarvis.errands.service import get_runner

router = APIRouter(prefix="/api/errands", tags=["errands"])


class AnswerBody(BaseModel):
    answers: str = Field(min_length=1, description="The user's answers, verbatim.")


def _summary(errand: Errand) -> dict[str, Any]:
    return {
        "id": errand.id,
        "goal": errand.goal,
        "state": str(errand.state),
        "plan": [{"intent": s.intent, "done": s.done} for s in errand.plan],
        "steps_taken": len(errand.steps),
        "open_questions": list(errand.open_questions),
        "outcome": errand.outcome,
        "language": errand.language,
        "created_at_ns": errand.created_at_ns,
        "updated_at_ns": errand.updated_at_ns,
    }


def _detail(errand: Errand) -> dict[str, Any]:
    body = _summary(errand)
    body["steps"] = [
        {
            "index": s.index,
            "intent": s.intent,
            "summary": s.summary,
            "tools_used": list(s.tools_used),
            "evidence": [
                {"kind": e.kind, "detail": e.detail, "source": e.source} for e in s.evidence
            ],
        }
        for s in errand.steps
    ]
    body["result_evidence"] = [
        {"kind": e.kind, "detail": e.detail, "source": e.source} for e in errand.result_evidence
    ]
    return body


def _runner_or_503() -> Any:
    runner = get_runner()
    if runner is None:
        raise HTTPException(
            status_code=503,
            detail="Errands are unavailable — the brain stack is not fully wired yet.",
        )
    return runner


@router.get("", summary="List recent errands")
async def list_errands(limit: int = 20) -> dict[str, Any]:
    runner = _runner_or_503()
    errands = await runner.store.list_recent(max(1, min(limit, 100)))
    return {"errands": [_summary(e) for e in errands]}


@router.get("/{errand_id}", summary="Inspect one errand, steps and proof included")
async def get_errand(errand_id: str) -> dict[str, Any]:
    runner = _runner_or_503()
    errand = await runner.store.get(errand_id)
    if errand is None:
        raise HTTPException(status_code=404, detail="No such errand.")
    return _detail(errand)


@router.post("/{errand_id}/answer", summary="Answer a waiting errand's questions")
async def answer_errand(errand_id: str, body: AnswerBody) -> dict[str, Any]:
    runner = _runner_or_503()
    errand = await runner.provide_answers(errand_id, body.answers.strip())
    if errand is None:
        raise HTTPException(status_code=404, detail="No such errand.")
    return _summary(errand)


@router.post("/{errand_id}/cancel", summary="Stop an errand")
async def cancel_errand(errand_id: str) -> dict[str, Any]:
    runner = _runner_or_503()
    errand = await runner.cancel(errand_id)
    if errand is None:
        raise HTTPException(status_code=404, detail="No such errand.")
    return _summary(errand)


__all__ = ["router"]
