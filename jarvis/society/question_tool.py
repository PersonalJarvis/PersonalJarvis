"""A focused user question for an interactive Society agent turn."""

from __future__ import annotations

from typing import Any

from jarvis.core.protocols import ToolResult


class AskUserTool:
    name = "society_ask_user"
    risk_tier = "safe"
    is_action_tool = False
    description = (
        "Ask the user only when a material ambiguity changes the outcome and cannot be "
        "settled from the request, available context, or a safe assumption. Never ask for "
        "optional preferences, routine runs, credentials, or an approval that another "
        "tool must obtain. Offer two to four distinct, actionable choices, each with a "
        "short consequence; identify the choice you recommend and why. The recommended "
        "choice is used after five minutes without an answer. This call pauses your "
        "current task; stop after calling it. You will receive a new turn containing "
        "the answer or timed default and can then continue."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "One concise decision question."},
            "options": {
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Short, distinct action."},
                        "description": {
                            "type": "string",
                            "description": "Practical effect of this choice.",
                        },
                    },
                    "required": ["label", "description"],
                },
            },
            "recommended_index": {
                "type": "integer",
                "description": "Zero-based index of your recommended option.",
            },
            "recommendation_reason": {
                "type": "string",
                "description": "Why this is the best default in this task.",
            },
        },
        "required": ["question", "options", "recommended_index", "recommendation_reason"],
    }

    def __init__(self, runtime: Any, agent_id: str, session_id: str) -> None:
        self._runtime = runtime
        self._agent_id = agent_id
        self._session_id = session_id

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        from jarvis.society.roster import AgentState

        rt = self._runtime
        if await rt.store.kill_switch():
            return ToolResult(success=False, output=None, error="The agent society is halted")
        agent = await rt.roster.get(self._agent_id)
        if agent is None or agent.state is not AgentState.ACTIVE:
            return ToolResult(success=False, output=None, error="The agent is unavailable")
        question = args.get("question")
        reason = args.get("recommendation_reason")
        raw_options = args.get("options")
        recommended = args.get("recommended_index")
        if not isinstance(question, str) or not question.strip() or len(question) > 500:
            return ToolResult(success=False, output=None, error="Question must be 1-500 characters")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            return ToolResult(success=False, output=None, error="Recommendation reason is required")
        if not isinstance(raw_options, list) or not 2 <= len(raw_options) <= 4:
            return ToolResult(success=False, output=None, error="Provide two to four options")
        options: list[dict[str, str]] = []
        for item in raw_options:
            if not isinstance(item, dict):
                return ToolResult(success=False, output=None, error="Invalid option")
            label, description = item.get("label"), item.get("description")
            if (
                not isinstance(label, str)
                or not label.strip()
                or len(label) > 100
                or not isinstance(description, str)
                or not description.strip()
                or len(description) > 300
            ):
                return ToolResult(
                    success=False,
                    output=None,
                    error="Each option needs a short label and consequence",
                )
            options.append({"label": label.strip(), "description": description.strip()})
        if len({row["label"].casefold() for row in options}) != len(options):
            return ToolResult(success=False, output=None, error="Options must be distinct")
        if type(recommended) is not int or not 0 <= recommended < len(options):
            return ToolResult(
                success=False, output=None, error="Recommendation must name an option"
            )
        service = rt.chat_service()
        if service is None:
            return ToolResult(success=False, output=None, error="Agent chat unavailable")
        try:
            question_id = await service.questions.create(
                self._session_id,
                agent_name=agent.name,
                question=question.strip(),
                options=options,
                recommended_index=recommended,
                recommendation_reason=reason.strip(),
            )
        except ValueError as exc:
            return ToolResult(success=False, output=None, error=str(exc))
        service.signal_cancel(self._session_id)
        return ToolResult(
            success=True,
            output={"question_id": question_id, "status": "waiting_for_answer"},
        )
