"""How a task's plugin grant covers a live tool name.

One rule, shared by the brain's per-task tool allowlist
(``BrainManager._select_task_tools``) and the unattended-approval bridge
(``TaskAutoApprover``), so a tool a routine can SEE is exactly a tool its
grant can pre-authorize.
"""

from __future__ import annotations

__all__ = ["grant_matches"]


def grant_matches(grant: str, tool_name: str) -> bool:
    """A grant names a tool exactly, or is a prefix grant for a bridged
    plugin whose tools are namespaced ``<plugin>/<tool>`` (``github`` covers
    ``github/list_issues``)."""
    return tool_name == grant or tool_name.startswith(grant + "/")
