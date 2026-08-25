"""local-models: the "Local models" section from a terminal.

What is installed on the pull-capable local server (Ollama today), which model
fills each role, the per-model option profile, the public catalogue, Hugging
Face GGUF browsing, and the server itself. Every command calls the same
``/api/providers/{provider}/local-models/...`` routes the section renders, so
the capability gate (a cloud card answers 400 with a sentence) and the config
writers are inherited, never re-implemented.

The provider defaults to ``ollama``; ``--provider`` names another pull-capable
card. Deleting a download, unloading it from memory, and stopping the server
are the destructive actions and need ``--yes``.
"""

from __future__ import annotations

import json
from typing import Any

import typer

from jarvis.cli_ctl import invoke, options

app = typer.Typer(
    no_args_is_help=True,
    help="Local models: roles, downloads, options, catalogue, Hugging Face, server.",
)

_PROVIDER = typer.Option("ollama", "--provider", help="Pull-capable provider id (default: ollama).")
_VALUES = typer.Argument(
    ..., help="KEY=VALUE pairs, e.g. num_ctx=8192 temperature=0.2 think=false."
)


def _base(provider: str) -> str:
    return f"/api/providers/{provider.strip() or 'ollama'}/local-models"


def _parse_value(raw: str) -> Any:
    """``KEY=VALUE`` values arrive as text; numbers, booleans and JSON lists
    become their typed form so the route's clamps see the real value."""
    text = raw.strip()
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    try:
        return json.loads(text)
    except ValueError:
        return text


def _pairs(items: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise typer.BadParameter(f"expected KEY=VALUE, got {item!r}")
        out[key.strip()] = _parse_value(value)
    return out


# ----------------------------------------------------------------------
# roles
# ----------------------------------------------------------------------

roles_app = typer.Typer(
    invoke_without_command=True,
    help="Which download fills each role (chat, tools & screen, deep, embedding).",
)
app.add_typer(roles_app, name="roles")


@roles_app.callback()
def roles_root(ctx: typer.Context, provider: str = _PROVIDER) -> None:
    """Bare ``roles`` lists, same as ``roles list``."""
    if ctx.invoked_subcommand is None:
        invoke.run("GET", f"{_base(provider)}/roles")


@roles_app.command("list")
def roles_list(provider: str = _PROVIDER) -> None:
    """Every role with its pick, what qualifies, and the recommendation."""
    invoke.run("GET", f"{_base(provider)}/roles")


@roles_app.command("set")
def roles_set(
    role: str = typer.Argument(..., help="chat | tools_screen | deep | embedding."),
    model: str = typer.Argument(
        "", help='Installed model tag; "" hands the role back to discovery.'
    ),
    provider: str = _PROVIDER,
    dry_run: bool = options.dry_opt(),
) -> None:
    """Assign a model to a role (written through the config writers)."""
    invoke.run(
        "PUT",
        f"{_base(provider)}/roles/{role}",
        body={"model": model},
        dangerous=False,
        dry_run=dry_run,
    )


# ----------------------------------------------------------------------
# models
# ----------------------------------------------------------------------

models_app = typer.Typer(
    invoke_without_command=True,
    help="The downloads on the server: list, show, unload, delete.",
)
app.add_typer(models_app, name="models")


@models_app.callback()
def models_root(ctx: typer.Context, provider: str = _PROVIDER) -> None:
    """Bare ``models`` lists, same as ``models list``."""
    if ctx.invoked_subcommand is None:
        invoke.run("GET", f"{_base(provider)}/inventory")


@models_app.command("list")
def models_list(provider: str = _PROVIDER) -> None:
    """Every download with its facts, what is loaded, and the disk total."""
    invoke.run("GET", f"{_base(provider)}/inventory")


@models_app.command("show")
def models_show(name: str, provider: str = _PROVIDER) -> None:
    """One download with the long facts (license, parameters, template)."""
    invoke.run("GET", f"{_base(provider)}/inventory/{name}")


@models_app.command("unload")
def models_unload(
    name: str,
    provider: str = _PROVIDER,
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Free the memory a loaded model holds; the next turn pays the load again."""
    invoke.run(
        "POST",
        f"{_base(provider)}/inventory/{name}/unload",
        dangerous=True,
        assume_yes=yes,
        dry_run=dry_run,
    )


@models_app.command("delete")
def models_delete(
    name: str,
    reassign: str = typer.Option(
        "",
        "--reassign",
        help="Installed model that takes over every role still pointing at NAME.",
    ),
    provider: str = _PROVIDER,
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Remove a download from the server (refused while a role still uses it)."""
    params = {"reassign": reassign.strip()} if reassign.strip() else None
    invoke.run(
        "DELETE",
        f"{_base(provider)}/inventory/{name}",
        params=params,
        dangerous=True,
        assume_yes=yes,
        dry_run=dry_run,
    )


# ----------------------------------------------------------------------
# options
# ----------------------------------------------------------------------

options_app = typer.Typer(
    no_args_is_help=True,
    help="Per-model option profiles (num_ctx, temperature, keep_alive, ...).",
)
app.add_typer(options_app, name="options")


@options_app.command("get")
def options_get(name: str, provider: str = _PROVIDER) -> None:
    """The profile of one model as configured (empty when none)."""
    invoke.run("GET", f"{_base(provider)}/models/{name}/options")


@options_app.command("set")
def options_set(
    name: str,
    values: list[str] = _VALUES,
    provider: str = _PROVIDER,
    dry_run: bool = options.dry_opt(),
) -> None:
    """Replace the profile of a model with the given knobs (whole set)."""
    invoke.run(
        "PUT",
        f"{_base(provider)}/models/{name}/options",
        body=_pairs(values),
        dangerous=False,
        dry_run=dry_run,
    )


@options_app.command("clear")
def options_clear(name: str, provider: str = _PROVIDER, dry_run: bool = options.dry_opt()) -> None:
    """Drop the profile so the server's defaults apply again."""
    invoke.run(
        "DELETE",
        f"{_base(provider)}/models/{name}/options",
        dangerous=False,
        dry_run=dry_run,
    )


@options_app.command("suggest")
def options_suggest(name: str, provider: str = _PROVIDER) -> None:
    """An advisory profile for this machine, with one reason per knob."""
    invoke.run("GET", f"{_base(provider)}/models/{name}/suggested-options")


# ----------------------------------------------------------------------
# catalog
# ----------------------------------------------------------------------

catalog_app = typer.Typer(
    no_args_is_help=True,
    help="The public model library: search, tags, the recommended shortlist.",
)
app.add_typer(catalog_app, name="catalog")


@catalog_app.command("search")
def catalog_search(
    query: str = typer.Argument("", help="Free text; empty lists the most popular."),
    sort: str = typer.Option("popular", "--sort", help="popular | newest."),
    capability: str = typer.Option(
        "", "--capability", help="tools | vision | embedding | thinking."
    ),
    limit: int = typer.Option(50, "--limit", min=1, max=50),
    provider: str = _PROVIDER,
) -> None:
    """Browse the library; offline is reported as a sentence, not a failure."""
    params: dict[str, Any] = {"q": query, "sort": sort, "limit": limit}
    if capability.strip():
        params["capability"] = capability.strip()
    invoke.run("GET", f"{_base(provider)}/catalog", params=params)


@catalog_app.command("tags")
def catalog_tags(name: str, provider: str = _PROVIDER) -> None:
    """Every tag of one library model with size, quantization, context and fit."""
    invoke.run("GET", f"{_base(provider)}/catalog/{name}/tags")


@catalog_app.command("recommended")
def catalog_recommended(provider: str = _PROVIDER) -> None:
    """The curated shortlist ranked for this machine, with its review date."""
    invoke.run("GET", f"{_base(provider)}/catalog/recommended")


# ----------------------------------------------------------------------
# hf
# ----------------------------------------------------------------------

hf_app = typer.Typer(
    no_args_is_help=True,
    help="Hugging Face GGUF browsing (off until switched on).",
)
app.add_typer(hf_app, name="hf")


@hf_app.command("search")
def hf_search(
    query: str = typer.Argument("", help="Free text over GGUF repositories."),
    sort: str = typer.Option(
        "downloads", "--sort", help="downloads | lastModified | trendingScore."
    ),
    limit: int = typer.Option(30, "--limit", min=1, max=100),
    provider: str = _PROVIDER,
) -> None:
    """GGUF repositories on Hugging Face."""
    invoke.run(
        "GET",
        f"{_base(provider)}/hf/search",
        params={"q": query, "sort": sort, "limit": limit},
    )


@hf_app.command("files")
def hf_files(
    user: str = typer.Argument(..., help="Repository owner."),
    repo: str = typer.Argument(..., help="Repository name."),
    provider: str = _PROVIDER,
) -> None:
    """The .gguf files of one repository with quantization, size and fit."""
    invoke.run("GET", f"{_base(provider)}/hf/{user}/{repo}/files")


@hf_app.command("pull")
def hf_pull(
    user: str = typer.Argument(..., help="Repository owner."),
    repo: str = typer.Argument(..., help="Repository name."),
    quant: str = typer.Option("", "--quant", help="Quantization tag, e.g. Q4_K_M."),
    provider: str = _PROVIDER,
    dry_run: bool = options.dry_opt(),
) -> None:
    """Start pulling hf.co/<user>/<repo>[:<quant>] through the normal pull path."""
    body: dict[str, Any] = {"user": user, "repo": repo}
    if quant.strip():
        body["quant"] = quant.strip()
    invoke.run("POST", f"{_base(provider)}/hf/pull", body=body, dangerous=False, dry_run=dry_run)


@hf_app.command("enable")
def hf_enable(
    state: str = typer.Argument("", help="on | off; empty shows the current switch."),
    provider: str = _PROVIDER,
    dry_run: bool = options.dry_opt(),
) -> None:
    """Show or flip the Hugging Face browsing switch."""
    choice = state.strip().lower()
    if not choice:
        invoke.run("GET", f"{_base(provider)}/hf/enabled")
        return
    if choice not in {"on", "off"}:
        raise typer.BadParameter("expected 'on' or 'off'")
    invoke.run(
        "PUT",
        f"{_base(provider)}/hf/enabled",
        body={"enabled": choice == "on"},
        dangerous=False,
        dry_run=dry_run,
    )


# ----------------------------------------------------------------------
# server
# ----------------------------------------------------------------------

server_app = typer.Typer(
    no_args_is_help=True,
    help="The local server: status, stop, probe a host, log, environment guide.",
)
app.add_typer(server_app, name="server")


@server_app.command("status")
def server_status(provider: str = _PROVIDER) -> None:
    """Runtime picture plus what is loaded and how much disk the downloads take."""
    invoke.run("GET", f"{_base(provider)}/server")


@server_app.command("stop")
def server_stop(
    provider: str = _PROVIDER,
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Stop the server Jarvis itself started (never one started elsewhere)."""
    invoke.run(
        "POST",
        f"{_base(provider)}/server/stop",
        dangerous=True,
        assume_yes=yes,
        dry_run=dry_run,
    )


@server_app.command("test")
def server_test(
    base_url: str = typer.Argument(..., help="Host to probe, e.g. http://127.0.0.1:11434."),
    provider: str = _PROVIDER,
) -> None:
    """Probe a host before saving it: version and latency, or why it failed."""
    invoke.run(
        "POST",
        f"{_base(provider)}/server/test",
        body={"base_url": base_url},
        dangerous=False,
    )


@server_app.command("log")
def server_log(
    lines: int = typer.Option(40, "--lines", min=1, max=500),
    provider: str = _PROVIDER,
) -> None:
    """The last lines of the server log Jarvis writes when it starts the server."""
    invoke.run("GET", f"{_base(provider)}/server/log", params={"lines": lines})


@server_app.command("env-guide")
def server_env_guide(
    os_name: str = typer.Option("", "--os", help="windows | macos | linux; empty = this OS."),
    provider: str = _PROVIDER,
) -> None:
    """Copyable per-OS commands for the server's environment variables."""
    params = {"os": os_name.strip()} if os_name.strip() else None
    invoke.run("GET", f"{_base(provider)}/server/env-guide", params=params)


# ----------------------------------------------------------------------
# assistant
# ----------------------------------------------------------------------

assistant_app = typer.Typer(
    no_args_is_help=True,
    help="The setup assistant: guided setup, diagnosis, the end-to-end test, benchmarks, health.",
)
app.add_typer(assistant_app, name="assistant")


_ROLE_FILTER = typer.Option(
    [],
    "--role",
    help="Restrict to a role (repeatable): chat, voice, tools_screen, deep, embedding.",
)


def _assistant(provider: str) -> str:
    return f"{_base(provider)}/assistant"


@assistant_app.command("setup")
def assistant_setup(provider: str = _PROVIDER) -> None:
    """Start a guided setup turn; answers with the session and turn ids."""
    invoke.run("POST", f"{_assistant(provider)}/run", body={"mode": "setup"}, dangerous=False)


@assistant_app.command("diagnose")
def assistant_diagnose(provider: str = _PROVIDER) -> None:
    """Ask the assistant what is broken and how to fix it."""
    invoke.run("POST", f"{_assistant(provider)}/run", body={"mode": "diagnose"}, dangerous=False)


@assistant_app.command("test")
def assistant_test(
    roles: list[str] = _ROLE_FILTER,
    provider: str = _PROVIDER,
) -> None:
    """Run the end-to-end setup test and print the per-role table."""
    body = {"roles": list(roles)} if roles else None
    invoke.run("POST", f"{_assistant(provider)}/test", body=body, dangerous=False)


@assistant_app.command("benchmarks")
def assistant_benchmarks(
    refresh: bool = typer.Option(False, "--refresh", help="Rebuild the table from the web."),
    provider: str = _PROVIDER,
) -> None:
    """The benchmark table behind the proven / new labels (cached seven days)."""
    params = {"refresh": "1"} if refresh else None
    invoke.run("GET", f"{_assistant(provider)}/benchmarks", params=params)


@assistant_app.command("health")
def assistant_health(provider: str = _PROVIDER) -> None:
    """The last self-check: status, reason, since when, last ok."""
    invoke.run("GET", f"{_assistant(provider)}/health")


@assistant_app.command("session")
def assistant_session(provider: str = _PROVIDER) -> None:
    """The assistant's session and whether the Agents tier can run it."""
    invoke.run("GET", f"{_assistant(provider)}/session")
