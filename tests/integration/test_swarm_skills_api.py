"""Mounted skill/recheck routes use actual team identities and typed null metrics."""

# ruff: noqa: F811 - imported pytest fixture is injected by parameter name

from tests.integration.test_swarm_api import api, create  # noqa: F401


def test_skills_scope_and_recheck_original_acceptance_requirement(api):
    client, _, _ = api
    team = create(
        client,
        tasks=[
            {
                "id": "work",
                "title": "Work",
                "description": "Produce proof",
                "acceptance": "Independently accepted output",
            }
        ],
    )
    foreign = create(client, "Foreign team")
    base = f"/api/swarm/teams/{team['id']}"
    reply = client.get(f"{base}/agents/{team['lead_id']}/skills")
    assert reply.status_code == 200, reply.text
    profile = reply.json()["profiles"][0]
    assert profile["acceptance"] == {"numerator": "0", "denominator": "0", "rate": None}
    assert profile["rollback"] == {"numerator": None, "denominator": None, "rate": None}
    assert client.get(f"{base}/agents/{foreign['lead_id']}/skills").status_code == 403
    assert client.get(f"{base}/agents/{team['lead_id']}/skills?limit=201").status_code == 422
    assert (
        client.post(f"{base}/tasks/work/recheck", json={"request_key": "review"}).status_code == 409
    )
    assert (
        client.post(
            f"{base}/tasks/foreign-task/recheck", json={"request_key": "review"}
        ).status_code
        == 403
    )
    operation = client.get("/openapi.json").json()["paths"][
        "/api/swarm/teams/{team_id}/tasks/{task_id}/recheck"
    ]["post"]
    assert operation["x-jarvis-dangerous"] is True
    assert operation["tags"] == ["swarm"]
