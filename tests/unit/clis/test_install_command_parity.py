"""The command a surface SHOWS must be the command that runs.

Two places used to word an install: ``CliInstaller.build_command``, which
builds the argv actually executed, and ``cli_routes._install_methods_of``,
which hand-assembled the same line again for the CLIs page. The copy drifted
the first time the npm line changed, and a page that shows one command while
the terminal runs another is worse than one that shows nothing.
"""

from __future__ import annotations

from jarvis.clis.installer import CliInstaller
from jarvis.clis.spec import AuthConfig, CliSpec, InstallMethods, RiskConfig
from jarvis.ui.web.cli_routes import _install_methods_of


def _spec(**install: object) -> CliSpec:
    return CliSpec(
        name="acme",
        display_name="Acme",
        description="",
        homepage="",
        binary_name="acme",
        check_command=("acme", "--version"),
        version_parse_regex=r"(\d+\.\d+\.\d+)",
        install=InstallMethods(**install),  # type: ignore[arg-type]
        auth=AuthConfig(type="none"),
        risk=RiskConfig(default_tier="monitor"),
        category="agent",
    )


def test_every_package_manager_shows_exactly_what_it_runs() -> None:
    spec = _spec(
        winget_id="Acme.Cli",
        scoop_package="acme",
        npm_package="@acme/cli",
        pip_package="acme-cli",
        cargo_package="acme-cli",
    )
    builder = CliInstaller()
    methods, _ = _install_methods_of(spec)

    shown = {m.manager: m.command for m in methods}
    assert set(shown) == {"winget", "scoop", "npm", "pip", "cargo"}
    for manager, command in shown.items():
        argv = builder.build_command(spec, manager)  # type: ignore[arg-type]
        assert argv is not None, manager
        assert command == " ".join(argv), manager


def test_the_npm_line_stays_verbose_where_the_user_reads_it() -> None:
    """The flags are the fix for a pane that looked dead; they must be visible.

    At its default log level ``npm install -g`` says nothing for the first
    ten-odd seconds and then redraws a one-character spinner. Someone watching
    the install terminal has only that to go on, so the flags are not a
    preference — and a page that hid them would be describing a quieter command
    than the one it starts.
    """
    methods, _ = _install_methods_of(_spec(npm_package="@acme/cli"))
    npm = next(m for m in methods if m.manager == "npm")
    assert npm.command == "npm install -g @acme/cli --loglevel=http --no-fund"


def test_a_script_install_still_shows_its_url_rather_than_the_wrapper() -> None:
    """Deliberately NOT the built argv: the URL is the thing worth reading.

    ``build_command`` wraps it in a platform download one-liner, which is what
    has to run and not what a person checks before agreeing to run it.
    """
    methods, recommended = _install_methods_of(_spec(script_url="https://acme.dev/install.sh"))
    assert [m.command for m in methods] == ["https://acme.dev/install.sh"]
    assert recommended == "script"


def test_an_entry_with_nothing_but_a_manual_page_says_so() -> None:
    methods, recommended = _install_methods_of(_spec(manual_url="https://acme.dev/download"))
    assert [(m.manager, m.command) for m in methods] == [("manual", "https://acme.dev/download")]
    assert recommended == "manual"
