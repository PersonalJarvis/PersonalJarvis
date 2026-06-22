"""telephony: outbound calling status + placing calls (/api/telephony)."""

from __future__ import annotations

import typer

from jarvis.cli_ctl import invoke, options

app = typer.Typer(no_args_is_help=True, help="Telephony: status, config, place outbound calls.")


@app.command()
def status() -> None:
    """Report telephony availability/status."""
    invoke.run("GET", "/api/telephony/status")


@app.command("config")
def config_get() -> None:
    """Show the telephony config."""
    invoke.run("GET", "/api/telephony/config")


@app.command()
def outbound(
    to: str = typer.Argument(..., help="Destination phone number (E.164)."),
    message: str = typer.Option(None, "--message", help="Spoken message / script."),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Place a real outbound call (destructive: costs money; needs --yes)."""
    body: dict[str, object] = {"to": to}
    if message:
        body["message"] = message
    invoke.run(
        "POST",
        "/api/telephony/outbound",
        body=body,
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
    )
