"""Bare `python -m jarvis` must start the app, never the terminal wizard.

Setup lives in the desktop/browser onboarding since 2026-07-06; the wizard is
an explicit opt-in (`--wizard`) for SSH-only hosts. If first-run state ever
feeds the wizard branch again, the installer's non-interactive contract and
the first-launch onboarding both silently break.
"""
import inspect

import jarvis.__main__ as main_mod


def test_wizard_only_on_explicit_flag() -> None:
    assert main_mod._should_run_wizard(False) is False
    assert main_mod._should_run_wizard(True) is True


def test_dispatch_source_no_longer_consults_first_run() -> None:
    src = inspect.getsource(main_mod)
    # The old auto-wizard trigger was `args.wizard or cfg.is_first_run()`.
    assert "args.wizard or cfg.is_first_run()" not in src
