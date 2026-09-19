"""Read measurable skill progress without manufacturing unobserved rates."""

from __future__ import annotations

import json
from typing import Any

from jarvis.core.swarm_reputation import AgentSkills

from .reputation import LEVEL_THRESHOLDS, initial_profile


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return dict(
        numerator=str(numerator),
        denominator=str(denominator),
        rate=numerator / denominator if denominator else None,
    )


def read_profiles(store: Any, agent_id: str, limit: int, offset: int) -> dict[str, Any]:
    from .store import SwarmAccessError

    with store._tx() as connection:
        store._team(connection)
        row = connection.execute("SELECT record FROM agents WHERE id=?", (agent_id,)).fetchone()
        if row is None:
            raise SwarmAccessError("No such agent in this team")
        agent = json.loads(row[0])
        profiles = [
            (item[0], json.loads(item[1]))
            for item in connection.execute(
                "SELECT domain,record FROM reputation WHERE agent_id=? "
                "ORDER BY domain LIMIT ? OFFSET ?",
                (agent_id, limit + 1, offset),
            )
        ]
        has_more = len(profiles) > limit
        profiles = profiles[:limit]
        if not profiles and offset == 0:
            profiles = [(agent["domain"], initial_profile())]
        result = []
        for domain, profile in profiles:
            # Only first-verification ratings form the reviewer acceptance denominator.
            # Later rechecks are distinct measurements, never another accepted task.
            totals = connection.execute(
                "SELECT coalesce(sum(v.accepted),0),count(*) FROM verifications v "
                "JOIN tasks t ON t.id=v.task_id WHERE json_extract(v.record,'$.agent_id')=? "
                "AND json_extract(t.record,'$.domain')=?",
                (agent_id, domain),
            ).fetchone()
            rechecks = connection.execute(
                "SELECT json_extract(record,'$.state'),count(*) FROM decisions "
                "WHERE json_extract(record,'$.kind')='contribution_recheck' "
                "AND json_extract(record,'$.agent_id')=? AND json_extract(record,'$.domain')=? "
                "GROUP BY json_extract(record,'$.state')",
                (agent_id, domain),
            ).fetchall()
            counts = {row[0]: row[1] for row in rechecks}
            checked = sum(counts.get(state, 0) for state in ("passed", "regression", "fabrication"))
            result.append(
                dict(
                    domain=domain,
                    **{
                        key: profile[key]
                        for key in (
                            "level",
                            "reliability",
                            "uncertainty",
                            "samples",
                            "verified_tasks",
                            "difficulty_counts",
                            "credits",
                        )
                    },
                    acceptance=_rate(int(totals[0]), int(totals[1])),
                    next_level_credits=next(
                        (value for value in LEVEL_THRESHOLDS if value > profile["credits"]),
                        None,
                    ),
                    regression=_rate(counts.get("regression", 0), checked),
                    rollback=dict(numerator=None, denominator=None, rate=None),
                )
            )
        history = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT record FROM ratings WHERE agent_id=? "
                "ORDER BY json_extract(record,'$.created_at') DESC,id DESC LIMIT 50",
                (agent_id,),
            )
        ]
    return AgentSkills.model_validate(
        dict(
            team_id=store.team_id,
            agent_id=agent_id,
            level=agent["level"],
            profiles=result,
            history=history,
            has_more=has_more,
        )
    ).model_dump(mode="json")
