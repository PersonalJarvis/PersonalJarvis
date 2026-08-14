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

TWO IDENTITIES
The vault holds accounts belonging to the user AND accounts the assistant holds
in its own name. Which one a task calls for is a real decision, not a detail:
signing in as the user puts the user's name on the action and leaves the user's
session behind in the browser profile, while signing in as itself is revocable
and attributable. So ``owner`` is a first-class argument here rather than
something inferred from the URL, and ``identity`` exists so the assistant can
answer "what address do I put in this form" without reaching for the user's.

Four actions, because they are four questions the brain genuinely asks:

``find``     before a login — "is there anything stored for this site?"
``list``     when the user asks what logins are held.
``report``   after a login — "that worked" / "that was refused".
``identity`` before acting as itself — "what are my own details?"

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
from jarvis.logins import identity as identity_module
from jarvis.logins import store as store_module
from jarvis.logins.store import CredentialOwner, LoginStatus

log = logging.getLogger(__name__)

#: Which side of the ledger an action applies to. ``any`` is the default for
#: reads because "is anything stored for this site" is a legitimate question,
#: and forcing a choice there would make the model guess before it has the
#: information the choice depends on. When both identities hold a login for
#: the same site, ``any`` resolves to the user's — the store prefers the
#: user's record in the unfiltered lookup, because a caller that named no
#: owner is acting on the user's behalf by default.
_OWNERS: dict[str, CredentialOwner | None] = {
    "user": CredentialOwner.USER,
    "agent": CredentialOwner.AGENT,
    "any": None,
}


def _whose(owner: CredentialOwner | None) -> str:
    """Phrase the owner for the model. Every result says whose account it is.

    Stated on every line rather than only when it was asked for: a model that
    requested any match and got one back would otherwise have no way to tell
    whether signing in with it puts the user's name on the action.
    """
    if owner is CredentialOwner.AGENT:
        return "your own account"
    if owner is CredentialOwner.USER:
        return "the user's account"
    return "owner not recorded"

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
        "Check whether a stored login exists for a website, list the stored "
        "logins, record whether a login attempt worked, or look up your OWN "
        "contact details. Call this with action='find' BEFORE trying to sign in "
        "anywhere — if an entry exists you get the service_id, the username and "
        "the notes about that site, and you then put SECRET(\"<service_id>\", "
        "\"password\") and SECRET(\"<service_id>\", \"username\") directly into "
        "your browser script. You never receive a password itself and you must "
        "never ask the user to tell you one — if no entry exists, say so and let "
        "them add it in the Passwords section. "
        "The vault holds two kinds of account: the user's own, and accounts you "
        "hold in your own name. Pass owner='user' when the task means acting on "
        "the user's behalf, owner='agent' when you are acting as yourself (your "
        "own signup, your own mailbox), and leave it out only when you genuinely "
        "just want to know whether anything is stored. Never sign in with the "
        "user's account for something you were asked to do in your own name. "
        "Use action='identity' to get your own address, phone number and "
        "handles before filling in a form about yourself; never put the user's "
        "details there, and never invent details you were not given. "
        "After a login attempt call action='report' with outcome='worked' or "
        "'rejected'; report what actually happened, because the user is asked to "
        "confirm every login until an entry has one successful report."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["find", "list", "report", "identity"],
                "description": (
                    "'find' to check one site, 'list' for all stored logins, "
                    "'report' to record how a login attempt went, 'identity' "
                    "for your own contact details."
                ),
            },
            "owner": {
                "type": "string",
                "enum": ["user", "agent", "any"],
                "description": (
                    "Whose account you mean. 'user' = the user's own account, "
                    "acting on their behalf. 'agent' = an account you hold in "
                    "your own name, acting as yourself. Defaults to 'any', "
                    "which reports what exists and returns the user's account "
                    "when both have a login for the site."
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
        {"action": "find", "url": "https://github.com/login", "owner": "agent"},
        {"action": "list"},
        {"action": "identity"},
        {"action": "report", "service_id": "github", "outcome": "worked"},
    ]

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        action = (args.get("action") or "").strip().lower()
        if action not in {"find", "list", "report", "identity"}:
            return ToolResult(
                success=False,
                output=None,
                error="action must be one of: find, list, report, identity.",
            )

        owner_arg = (args.get("owner") or "any").strip().lower()
        if owner_arg not in _OWNERS:
            return ToolResult(
                success=False,
                output=None,
                error="owner must be one of: user, agent, any.",
            )
        owner = _OWNERS[owner_arg]

        # Answered from the identity profile, not the vault — it needs no store
        # and must still work when the vault itself cannot be opened.
        if action == "identity":
            return self._identity()

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
            return self._list(store, owner)
        if action == "find":
            return self._find(store, args, owner)
        return self._report(store, args)

    @staticmethod
    def _identity() -> ToolResult:
        """The assistant's own contact details, for forms about itself."""
        try:
            profile = identity_module.default_identity_store().load()
        except Exception as exc:  # noqa: BLE001 -- never crash a turn on this
            log.warning("agent identity unavailable: %r", exc)
            return ToolResult(
                success=False,
                output=None,
                error=(
                    "Your own identity profile could not be read, so I cannot "
                    "tell you which address or number is yours. Do not guess "
                    "and do not use the user's details."
                ),
            )

        fallback = ""
        try:
            from jarvis.brain.assistant_name import (  # noqa: PLC0415
                resolve_assistant_name,
            )
            from jarvis.core.config import load_config  # noqa: PLC0415

            fallback = resolve_assistant_name(load_config())
        except Exception:  # noqa: BLE001 -- the name is a nicety, not the answer
            log.debug("could not resolve the assistant name for the identity card")

        log.info("agent identity read (configured: %s)", profile.is_configured)
        return ToolResult(success=True, output=profile.describe_for_model(fallback))

    @staticmethod
    def _list(store: Any, owner: CredentialOwner | None) -> ToolResult:
        summaries = store.list_summaries(owner)
        if not summaries:
            scope = ""
            if owner is CredentialOwner.USER:
                scope = " for the user"
            elif owner is CredentialOwner.AGENT:
                scope = " in your own name"
            return ToolResult(
                success=True,
                output=(
                    f"No logins are stored{scope} yet. The user can add one in "
                    "the Passwords section."
                ),
            )
        lines = [
            f"- {s.label} (service_id: {s.service_id}, {_whose(s.owner)}) — "
            f"{s.username} on {', '.join(s.domains) or 'no domain set'} "
            f"[{s.status.value}]"
            for s in summaries
        ]
        return ToolResult(
            success=True,
            output=f"{len(summaries)} stored login(s):\n" + "\n".join(lines),
        )

    @staticmethod
    def _find(
        store: Any, args: dict[str, Any], owner: CredentialOwner | None
    ) -> ToolResult:
        target = (args.get("url") or args.get("service_id") or "").strip()
        if not target:
            return ToolResult(
                success=False,
                output=None,
                error="Pass the url (or service_id) you want to check.",
            )

        credential = store.find_for_url(target, owner)
        if credential is None and args.get("service_id"):
            loaded = store.load(str(args["service_id"]).strip())
            # A service_id names one exact record, so honour the owner filter
            # here too — otherwise asking for the assistant's own account and
            # naming an id would quietly hand back the user's.
            if loaded is not None and (owner is None or loaded.owner is owner):
                credential = loaded
        if credential is None:
            log.info("no stored login for %s (owner=%s)", target, owner or "any")
            return ToolResult(
                success=True,
                output=(
                    f"No stored login for {target}"
                    + (f" {_whose(owner)}" if owner is not None else "")
                    + ". Do not ask the user to dictate a password — tell them "
                    "they can add it in the Passwords section, then try again."
                ),
            )

        summary = credential.summary()
        log.info("stored login found for %s: %s", target, summary.service_id)
        parts = [
            f"Stored login found for {summary.label} — this is {_whose(summary.owner)}.",
            f"service_id: {summary.service_id}",
            f"kind: {summary.kind}",
            f"username: {summary.username}",
            f"password: stored — use SECRET(\"{summary.service_id}\", \"password\")",
        ]
        if summary.has_totp:
            parts.append(
                f'two-factor seed: stored — use SECRET("{summary.service_id}", "totp")'
            )
        for name in summary.secret_names:
            parts.append(
                f'{name}: stored — use SECRET("{summary.service_id}", "{name}")'
            )
        for name, value in summary.fields:
            # Non-secret detail, so the value itself is safe to state — it saves
            # a round trip when the model only needs an address or a base URL.
            parts.append(f"{name}: {value}")
        if summary.owner is CredentialOwner.AGENT:
            parts.append(
                "This account is yours. Sign in with it only for work you are "
                "doing in your own name."
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
