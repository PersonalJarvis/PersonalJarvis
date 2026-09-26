"""Portable reply expectations shared by lead and teammate communication.

Policies live in the existing envelope JSON, not in delivery status. Old
assignments still report completion; information and answers close quietly.
"""

from __future__ import annotations

from typing import Any

from .events import MsgType, SocietyEnvelope

REPLY_POLICIES = ("none", "on_error", "always")
REPLY_POLICY_SCHEMA: dict[str, Any] = {
    "type": "string",
    "enum": list(REPLY_POLICIES),
    "description": (
        "Expected reply: none for information or an explicitly silent task; "
        "on_error for work needing only blockers reported; always for a question, "
        "requested result or decision. Choose from the user's intent and context."
    ),
}

COMMUNICATION_GUIDANCE = """## Communicating with teammates
Compose an independently actionable brief, not a lightly reworded user sentence.
Use the relevant conversation: explain the objective, known target and context,
concrete scope, constraints, and what evidence or output establishes completion.
Keep simple requests short; include detail only when it helps the recipient act.
Never invent URLs, paths, credentials, prior decisions or additional authorization.
Resolve references such as "that page" from known context; identify missing facts.
For "tell X to do Y", delegate the actual work with delegate_to_agent when available.
For "ask X about Y", send a query with the precise question and relevant context.
Use say for information, propose for a decision, answer for a substantive reply.
Choose reply_policy explicitly: always for requested findings or an answer,
on_error for work with no requested success report, none for information or an
explicit request for silence. A query always needs an answer. Answers normally
end the exchange; do not send acknowledgements, thanks, or acknowledgement loops.
Use reply_status=blocked for an inability to finish or a missing prerequisite.
Necessary clarification uses a concrete query, not a generic "shall I proceed?".
Delivery is not task completion, and a promise to act is not evidence of success.
Reply to the requesting agent; the lead conveys requested findings to the user.
Example: "test your browser" becomes a check of opening a public test page,
reading its content and following a link with the granted browser tool; record
observations and distinguish browser-access failures from page failures. Use a
known target when provided. Do not turn this into an unrelated full product audit.
"""


def select_reply_policy(value: Any, kind: MsgType) -> str:
    """Validate tool input, preserving compatible defaults for older callers."""
    default = "always" if kind in (MsgType.ASSIGN, MsgType.QUERY, MsgType.PROPOSE) else "none"
    policy = default if value is None else value
    if policy not in REPLY_POLICIES:
        raise ValueError("reply_policy must be none, on_error or always")
    if kind is MsgType.QUERY and policy != "always":
        raise ValueError("a query requires reply_policy=always")
    if kind is MsgType.ANSWER and policy != "none":
        raise ValueError("an answer closes the exchange; use a query for a new question")
    return policy


def reply_policy(env: SocietyEnvelope) -> str:
    """Read older envelopes without failing the receiver on malformed metadata."""
    try:
        return select_reply_policy(env.payload.get("reply_policy"), env.msg_type)
    except (TypeError, ValueError):
        # Invalid old metadata must not hide a requested result.
        return "always" if env.msg_type in (MsgType.ASSIGN, MsgType.QUERY) else "none"


def should_report(env: SocietyEnvelope, status: str) -> bool:
    policy = reply_policy(env)
    return policy == "always" or (policy == "on_error" and status not in {"done", "reported"})


def response_instruction(env: SocietyEnvelope) -> str:
    policy = reply_policy(env)
    if env.msg_type is MsgType.ASSIGN:
        return (
            f"Reply expectation: {policy}. Before your final response, call "
            "society_report_outcome with the actual status, result, output and open work. "
            "Use blocked for a missing login, approval or decision and partial for unfinished "
            "work. The runtime routes completion; do not send a second success message. "
            "A finished chat turn alone does not prove the task succeeded."
        )
    if policy == "always":
        return (
            "Reply to the sender using society_message_agent with kind 'answer'. "
            "Include the actual findings or decision; use reply_status=blocked if you "
            "cannot answer. This is internal communication; do not use an external "
            "messaging connector. No preliminary acknowledgement is needed."
        )
    if policy == "on_error":
        return (
            "Reply only for a blocker or missing prerequisite, using society_message_agent "
            "with kind 'answer' and reply_status=blocked. Keep successful completion in "
            "your own chat; do not send a success acknowledgement."
        )
    return (
        "No reply is requested. Use this information and finish in your own chat. "
        "Do not send an acknowledgement or reply to an answer. A genuinely new question "
        "or material blocker may start a separate, substantive exchange."
    )
