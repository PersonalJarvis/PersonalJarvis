"""``credentials`` tool — does a stored login exist, and did it work?

Router-tier. This is the brain's ONLY window into the login vault, and it is
deliberately a narrow one: it answers *whether* a login exists, for which
service, under which username, and what the user wrote down about it. It never
returns a password, and there is no action here that could return one.

The value itself travels a different road entirely — the model puts
``SECRET("<service_id>", "password")`` into a browser script and
``jarvis.logins.injection`` fills it in on the way to the harness process. So
the split is: this tool is the part that *knows*, the injection layer is the
part that *fills*, and only the second one ever touches the secret. That is the
same division a password manager's UI and autofill make, and it is what keeps a
password out of the transcript.

Three actions, because they are three questions the brain genuinely asks:

``find``   before a login — "is there anything stored for this site?"
``list``   when the user asks what logins Jarvis holds.
``report`` after a login — "that worked" / "that was refused".

``report`` is what makes the confirmation rule work. A credential is unproven
until a login with it succeeds, and the browser tool asks for confirmation
while it is unproven. Without an honest report the entry never becomes proven
and every login keeps asking; with a dishonest one an entry looks proven when
it is not. So the description below tells the model plainly to report what
actually happened, and the tool does not guess on its own.

Risk tier ``safe``: every action is either a metadata read or a status stamp on
the user's own record. Nothing here is destructive and nothing leaves the
machine, so a confirmation nag would only train the user to click through.
"""

from __future__ import annotations

import logging
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.logins import store as store_module
from jarvis.logins.store import LoginStatus

log = logging.getLogger(__name__)

#: What the model may report back after a login attempt, mapped to the stored
#: status. Deliberately two words rather than a free-text field: an open string
#: would drift into "probably fine" and the confirmation rule would rot.
_OUTCOMES: dict[str, LoginStatus] = {
    "worked": LoginStatus.OK,
    "rejected": LoginStatus.REJECTED,
}


class CredentialsTool:
    """Read the login vault's metadata, and record whether a login worked."""

    name: str = "credentials"
    risk_tier: str = "safe"
    description: str = (
        "Check whether the user has a stored login for a website, list the "
        "stored logins, or record whether a login attempt worked. Call this "
        "with action='find' BEFORE trying to sign in anywhere — if an entry "
        "exists you get the service_id, the username and the user's own notes "
        "about that site, and you then put SECRET(\"<service_id>\", "
        "\"password\") and SECRET(\"<service_id>\", \"username\") directly into "
        "your browser script. You never receive the password itself and you "
        "must never ask the user to tell you one — if no entry exists, say so "
        "and let them add it in the Passwords section. After a login attempt "
        "call action='report' with outcome='worked' or 'rejected'; report what "
        "actually happened, because the user is asked to confirm every login "
        "until an entry has one successful report."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["find", "list", "report"],
                "description": (
                    "'find' to check one site, 'list' for all stored logins, "
                    "'report' to record how a login attempt went."
                ),
            },
            "url": {
                "type": "string",
                "description": (
                    "For 'find': the page you are about to log in to, e.g. "
                    "'https://github.com/login' or just 'github.com'. "
                    "Subdomains match a stored parent domain."
                ),
            },
            "service_id": {
                "type": "string",
                "description": (
                    "For 'report' (and as an alternative to url for 'find'): "
                    "the service_id a previous 'find' returned."
                ),
            },
            "outcome": {
                "type": "string",
                "enum": ["worked", "rejected"],
                "description": "For 'report': what actually happened.",
            },
        },
        "required": ["action"],
    }
    input_examples: list[dict[str, Any]] = [
        {"action": "find", "url": "https://github.com/login"},
        {"action": "list"},
        {"action": "report", "service_id": "github", "outcome": "worked"},
    ]

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        action = (args.get("action") or "").strip().lower()
        if action not in {"find", "list", "report"}:
            return ToolResult(
                success=False,
                output=None,
                error="action must be one of: find, list, report.",
            )

        try:
            store = store_module.default_store()
        except Exception as exc:  # noqa: BLE001 -- report, never crash the turn
            log.warning("login vault unavailable: %r", exc)
            return ToolResult(
                success=False,
                output=None,
                error=(
                    "The login vault could not be opened, so I cannot tell "
                    "whether a login is stored. The credential store may be "
                    "locked — ask the user to open the Passwords section once."
                ),
            )

        if action == "list":
            return self._list(store)
        if action == "find":
            return self._find(store, args)
        return self._report(store, args)

    @staticmethod
    def _list(store: Any) -> ToolResult:
        summaries = store.list_summaries()
        if not summaries:
            return ToolResult(
                success=True,
                output=(
                    "No logins are stored yet. The user can add one in the "
                    "Passwords section."
                ),
            )
        lines = [
            f"- {s.label} (service_id: {s.service_id}) — {s.username} "
            f"on {', '.join(s.domains) or 'no domain set'} [{s.status.value}]"
            for s in summaries
        ]
        return ToolResult(
            success=True,
            output=f"{len(summaries)} stored login(s):\n" + "\n".join(lines),
        )

    @staticmethod
    def _find(store: Any, args: dict[str, Any]) -> ToolResult:
        target = (args.get("url") or args.get("service_id") or "").strip()
        if not target:
            return ToolResult(
                success=False,
                output=None,
                error="Pass the url (or service_id) you want to check.",
            )

        credential = store.find_for_url(target)
        if credential is None and args.get("service_id"):
            credential = store.load(str(args["service_id"]).strip())
        if credential is None:
            log.info("no stored login for %s", target)
            return ToolResult(
                success=True,
                output=(
                    f"No stored login for {target}. Do not ask the user to dictate "
                    "a password — tell them they can add it in the Passwords "
                    "section, then try again."
                ),
            )

        summary = credential.summary()
        log.info("stored login found for %s: %s", target, summary.service_id)
        parts = [
            f"Stored login found for {summary.label}.",
            f"service_id: {summary.service_id}",
            f"username: {summary.username}",
            f"password: stored — use SECRET(\"{summary.service_id}\", \"password\")",
        ]
        if summary.has_totp:
            parts.append(
                f'two-factor seed: stored — use SECRET("{summary.service_id}", "totp")'
            )
        if summary.status is LoginStatus.REJECTED:
            parts.append(
                "NOTE: the last login with this entry was refused. It may be "
                "out of date — if it fails again, tell the user rather than retrying."
            )
        if summary.notes:
            parts.append(f"\nThe user's notes for this site:\n{summary.notes}")
        return ToolResult(success=True, output="\n".join(parts))

    @staticmethod
    def _report(store: Any, args: dict[str, Any]) -> ToolResult:
        service_id = (args.get("service_id") or "").strip()
        outcome = (args.get("outcome") or "").strip().lower()
        if not service_id:
            return ToolResult(
                success=False,
                output=None,
                error="Pass the service_id you logged in with.",
            )
        if outcome not in _OUTCOMES:
            return ToolResult(
                success=False,
                output=None,
                error="outcome must be 'worked' or 'rejected'.",
            )
        if store.load(service_id) is None:
            return ToolResult(
                success=False,
                output=None,
                error=f"There is no stored login with service_id {service_id!r}.",
            )

        store.mark_used(service_id, _OUTCOMES[outcome])
        log.info("login outcome for %s: %s", service_id, outcome)
        if outcome == "rejected":
            return ToolResult(
                success=True,
                output=(
                    f"Recorded that the login for {service_id} was refused. Tell "
                    "the user the stored password may need updating in the "
                    "Passwords section — do not keep retrying it."
                ),
            )
        return ToolResult(
            success=True,
            output=(
                f"Recorded that the login for {service_id} worked. Future logins "
                "there will run without asking the user first."
            ),
        )


__all__ = ["CredentialsTool"]
