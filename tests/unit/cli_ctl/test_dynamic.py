import click
import pytest

from jarvis.cli_ctl import dynamic
from jarvis.cli_ctl.dynamic import _clean_name


@pytest.mark.parametrize(
    "op_id, method, path, expected",
    [
        # FastAPI: re.sub(r"\W", "_", <func><path>) + "_<method>"
        ("list_clis_api_clis_get", "get", "/api/clis", "list-clis"),
        ("get_cli_api_clis__name__get", "get", "/api/clis/{name}", "get-cli"),
        ("check_cli_api_clis__name__check_post", "post",
         "/api/clis/{name}/check", "check-cli"),
        ("restart_app_api_settings_restart_app_post", "post",
         "/api/settings/restart-app", "restart-app"),
        ("", "get", "/api/ping", "get-api-ping"),  # no operationId -> fallback
    ],
)
def test_clean_name_recovers_function_name(op_id, method, path, expected):
    assert _clean_name(op_id, method, path) == expected


SPEC = {
    "openapi": "3.1.0",
    "info": {"version": "1"},
    "paths": {
        "/api/tasks": {
            "get": {
                "tags": ["tasks"],
                "operationId": "list_tasks_api_tasks_get",
                "summary": "List tasks",
                "parameters": [
                    {"name": "limit", "in": "query",
                     "schema": {"type": "integer"}, "required": False}
                ],
            }
        },
        "/api/tasks/{task_id}": {
            "get": {
                "tags": ["tasks"],
                "operationId": "get_task",
                "summary": "Get task",
                "parameters": [
                    {"name": "task_id", "in": "path",
                     "schema": {"type": "string"}, "required": True}
                ],
            }
        },
    },
}


def test_builds_group_per_tag_with_commands():
    captured = {}

    def runner(method, path, params, body):
        captured.update(method=method, path=path, params=params, body=body)
        return {"ok": True}

    grp = dynamic.build_api_group(SPEC, runner)
    assert isinstance(grp, click.Group)
    tasks = grp.get_command(None, "tasks")
    assert isinstance(tasks, click.Group)
    names = set(tasks.list_commands(None))
    assert "list-tasks" in names or "get-task" in names


def test_path_param_substituted_and_query_passed():
    captured = {}

    def runner(method, path, params, body):
        captured.update(method=method, path=path, params=params, body=body)
        return {}

    grp = dynamic.build_api_group(SPEC, runner)
    cmd = grp.get_command(None, "tasks").get_command(None, "get-task")
    cmd.callback(task_id="abc")
    assert captured["path"] == "/api/tasks/{task_id}".replace("{task_id}", "abc")
