"""brain: inspect and switch the active brain + sub-agent providers.

Flagship example: `jarvis brain switch openai` changes the active main brain
provider; `jarvis brain subagent-switch openai` changes the worker/sub-agent
provider (e.g. Codex -> OpenAI). Switches are reversible (config + live), so they
proceed without --yes; pass --no-persist to apply live-only.
"""
from __future__ import annotations

import typer

from jarvis.cli_ctl import invoke, options

app = typer.Typer(
    no_args_is_help=True,
    help="Brain providers: status, switch, sub-agent switch, models, test.",
)

_PROVIDER_HELP = (
    "Provider id: claude-api | openrouter | openai | gemini | grok | codex | "
    "antigravity (the server validates the value)."
)


@app.command()
def status() -> None:
    """Show configured providers and which one is active."""
    invoke.run("GET", "/api/providers")


@app.command("list")
def list_providers() -> None:
    """List configured brain providers (alias of status)."""
    invoke.run("GET", "/api/providers")


@app.command()
def switch(
    provider: str = typer.Argument(..., help=_PROVIDER_HELP),
    persist: bool = options.persist_opt(),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Switch the ACTIVE main brain provider (e.g. `jarvis brain switch openai`)."""
    invoke.run(
        "POST", "/api/brain/switch",
        body={"provider": provider, "persist": persist},
        assume_yes=yes, dry_run=dry_run, dangerous=False,
    )


@app.command("subagent-switch")
def subagent_switch(
    provider: str = typer.Argument(..., help=_PROVIDER_HELP),
    persist: bool = options.persist_opt(),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Switch the sub-agent / worker provider (e.g. Codex -> OpenAI)."""
    invoke.run(
        "POST", "/api/subagent/switch",
        body={"provider": provider, "persist": persist},
        assume_yes=yes, dry_run=dry_run, dangerous=False,
    )


@app.command()
def test(
    provider: str = typer.Argument(..., help="Provider id to test."),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Test connectivity + auth for a provider."""
    invoke.run("POST", f"/api/providers/{provider}/test", dry_run=dry_run, dangerous=False)


@app.command("deep-model")
def deep_model(
    model: str = typer.Argument(..., help="Model id for the sub-agent deep brain."),
    persist: bool = options.persist_opt(),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Set the sub-agent deep model."""
    invoke.run(
        "POST", "/api/subagent/model",
        body={"model": model, "persist": persist},
        assume_yes=yes, dry_run=dry_run, dangerous=False,
    )
