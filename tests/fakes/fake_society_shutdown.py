"""Unrelated server resources for exercising the real Society shutdown path."""

from types import SimpleNamespace

from fastapi import FastAPI

from jarvis.ui.web.server import WebServer


class RecordingProcessTree:
    """Record containment release while a real child is reaped independently."""

    def __init__(self):
        self.closes = 0

    def close(self):
        self.closes += 1


def society_shutdown_server(runtime):
    """Skip boot composition, preserving the entire production stop method."""
    cleaned = []

    def sync_cleanup(name):
        return lambda *_args, **_kwargs: cleaned.append(name)

    def async_cleanup(name):
        async def action(*_args, **_kwargs):
            cleaned.append(name)

        return action

    server = WebServer.__new__(WebServer)
    server.app = FastAPI()
    server.app.state.society = runtime
    server.app.state.agent_chat = SimpleNamespace(cancel_all=async_cleanup("chat"))
    for name in (
        "_browser_prepare_task",
        "_board_aggregator_task",
        "_realtime_warm_task",
        "_bio_scheduler",
        "_board_evaluator",
        "_board_aggregator",
        "_task_scheduler",
        "_task_cancel_token",
        "_task_scheduler_task",
        "_task_store",
        "_skill_registry",
        "_doc_registry",
        "_plugin_registry",
        "_server",
        "_serve_task",
    ):
        setattr(server, name, None)
    server._mic_level_sessions = set()
    server._stop_mic_level_bridge = sync_cleanup("mic-bridge")
    server._stop_marketplace_refresh_scheduler = async_cleanup("marketplace-refresh")
    server._stop_local_models_health_monitor = async_cleanup("model-health")
    server._stop_local_models_autostart = async_cleanup("model-autostart")
    server._pty = SimpleNamespace(close_all=sync_cleanup("pty"))
    server._mission_tool_approvals = SimpleNamespace(deny_all=async_cleanup("mission-approvals"))
    return server, cleaned
