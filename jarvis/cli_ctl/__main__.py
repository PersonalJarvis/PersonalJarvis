"""jarvisctl entry point. Real commands are wired in here."""
from __future__ import annotations

import logging
import os
import sys

import click
import typer

# Windows defaults to cp1252; force UTF-8 so non-ASCII help/output is intact.
try:  # reconfigure exists on TextIO in 3.7+; guard for exotic stdio wrappers
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except (AttributeError, ValueError):  # pragma: no cover - exotic stdio
    pass

from jarvis.cli_ctl import config as _config
from jarvis.cli_ctl import openapi_cache
from jarvis.cli_ctl.client import JarvisClient
from jarvis.cli_ctl.commands import auth as auth_cmd
from jarvis.cli_ctl.commands import board as board_cmd
from jarvis.cli_ctl.commands import brain as brain_cmd
from jarvis.cli_ctl.commands import conductor as conductor_cmd
from jarvis.cli_ctl.commands import config as config_cmd
from jarvis.cli_ctl.commands import contacts as contacts_cmd
from jarvis.cli_ctl.commands import docs as docs_cmd
from jarvis.cli_ctl.commands import frontier as frontier_cmd
from jarvis.cli_ctl.commands import marketplace as marketplace_cmd
from jarvis.cli_ctl.commands import mcps as mcps_cmd
from jarvis.cli_ctl.commands import missions as missions_cmd
from jarvis.cli_ctl.commands import outputs as outputs_cmd
from jarvis.cli_ctl.commands import sessions as sessions_cmd
from jarvis.cli_ctl.commands import skills as skills_cmd
from jarvis.cli_ctl.commands import system as system_cmd
from jarvis.cli_ctl.commands import tasks as tasks_cmd
from jarvis.cli_ctl.commands import telephony as telephony_cmd
from jarvis.cli_ctl.commands import wiki as wiki_cmd
from jarvis.cli_ctl.commands import workflows as workflows_cmd

app = typer.Typer(
    name="jarvisctl",
    no_args_is_help=True,
    add_completion=True,
    help="Control a running Personal Jarvis instance from the terminal.",
)

# Shared state set by the root callback and read by commands.
STATE: dict[str, object] = {"json": False, "url": None, "key": None}


@app.callback()
def _root(
    json_output: bool = typer.Option(
        False, "--json", help="Emit raw JSON instead of human tables."
    ),
    url: str | None = typer.Option(
        None, "--url", help="Override the Jarvis base URL (e.g. http://host:port)."
    ),
    key: str | None = typer.Option(
        None, "--key", hide_input=True,
        help="Override the control API key (Bearer token) for this call only.",
    ),
) -> None:
    """jarvisctl — thin HTTP control client for a running Jarvis server."""
    STATE["json"] = json_output
    STATE["url"] = url
    STATE["key"] = key


def as_json() -> bool:
    return bool(STATE["json"])


def make_client(url: str | None = None, key: str | None = None) -> JarvisClient:
    """Build a client from explicit overrides, the ``--url`` / ``--key`` global
    options, or the resolved profile (env / config / live session / local key)."""
    prof = _config.resolve_profile()
    return JarvisClient(
        base_url=url or STATE.get("url") or prof.base_url,  # type: ignore[arg-type]
        control_key=key or STATE.get("key") or prof.control_key,  # type: ignore[arg-type]
    )


@app.command()
def version() -> None:
    """Print the jarvisctl version."""
    from jarvis import __version__

    typer.echo(f"jarvisctl (Personal Jarvis {__version__})")


@app.command()
def refresh() -> None:
    """Clear the cached API schema (next call re-fetches it)."""
    openapi_cache.clear_cache()
    typer.echo("Schema cache cleared.")


app.add_typer(auth_cmd.app, name="auth")
app.add_typer(system_cmd.app, name="system")
app.add_typer(tasks_cmd.app, name="tasks")
app.add_typer(brain_cmd.app, name="brain")
app.add_typer(config_cmd.app, name="config")
app.add_typer(missions_cmd.app, name="missions")
app.add_typer(wiki_cmd.app, name="wiki")
app.add_typer(sessions_cmd.app, name="sessions")
app.add_typer(skills_cmd.app, name="skills")
app.add_typer(outputs_cmd.app, name="outputs")
app.add_typer(board_cmd.app, name="board")
app.add_typer(workflows_cmd.app, name="workflows")
app.add_typer(conductor_cmd.app, name="conductor")
app.add_typer(contacts_cmd.app, name="contacts")
app.add_typer(telephony_cmd.app, name="telephony")
app.add_typer(marketplace_cmd.app, name="marketplace")
app.add_typer(mcps_cmd.app, name="mcps")
app.add_typer(docs_cmd.app, name="docs")
app.add_typer(frontier_cmd.app, name="frontier")


def _in_completion() -> bool:
    # Typer/Click set a *_COMPLETE env var during shell completion. Never do
    # network I/O on that hot path — use cache-only.
    return any(k.endswith("_COMPLETE") for k in os.environ)


def _dynamic_runner(method, path, params, body):
    with make_client() as client:
        return client.request(method, path, params=params, json=body)


def build_root_command() -> click.Group:
    """Return the Click root: the Typer app plus the grafted dynamic `api` group."""
    root: click.Group = typer.main.get_command(app)
    try:
        if _in_completion():
            # cache-only: ttl effectively infinite, no fetch attempt
            spec, _ = openapi_cache._read_cache()
        else:
            with make_client() as client:
                spec = openapi_cache.load_spec(client)
        if spec:
            from jarvis.cli_ctl.dynamic import build_api_group

            root.add_command(build_api_group(spec, _dynamic_runner))
    except Exception as exc:  # noqa: S110 - static surface must work if dynamic build fails
        # The static surface must always work even if the dynamic build fails;
        # log at DEBUG so a missing `api` group stays diagnosable.
        logging.getLogger(__name__).debug("dynamic api group unavailable: %s", exc)
    return root


def main() -> None:
    build_root_command()()


if __name__ == "__main__":
    main()
