"""auth: store/verify the control key for a Jarvis target."""
from __future__ import annotations

import typer

from jarvis.cli_ctl import config, render
from jarvis.cli_ctl.client import ApiError

app = typer.Typer(no_args_is_help=True, help="Authenticate against a Jarvis server.")

_PROBE = "/api/control/auth/probe"


def _probe(url: str, key: str) -> bool:
    # Local import avoids a circular import with __main__ at module load.
    from jarvis.cli_ctl.__main__ import make_client

    try:
        with make_client(url=url, key=key) as client:
            client.request("GET", _PROBE)
        return True
    except ApiError:
        return False


@app.command()
def login(
    url: str = typer.Option(..., "--url", help="Base URL, e.g. http://127.0.0.1:47821"),
    key: str = typer.Option(..., "--key", help="Control key (jctl_…)."),
) -> None:
    """Verify the key against the server and persist it for future calls."""
    if not _probe(url, key):
        render.error("control key rejected or server unreachable; not saved.")
        raise typer.Exit(code=1)
    config.save_login(url, key)
    typer.echo(f"Logged in to {url}.")


@app.command()
def status(
    url: str = typer.Option(None, "--url"),
    key: str = typer.Option(None, "--key"),
) -> None:
    """Report whether the configured (or given) target is reachable."""
    from jarvis.cli_ctl.__main__ import as_json

    prof = config.resolve_profile()
    target = url or prof.base_url
    use_key = key or prof.control_key or ""
    reachable = _probe(target, use_key)
    render.emit(
        {"base_url": target, "reachable": reachable}, as_json=as_json()
    )
    if not reachable:
        raise typer.Exit(code=1)


@app.command()
def logout() -> None:
    """Forget the saved credentials."""
    config.clear_login()
    typer.echo("Logged out.")
