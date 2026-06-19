"""The package imports and exposes a Typer `app` and a `main` callable."""
def test_package_exposes_app_and_main():
    import typer

    from jarvis.cli_ctl import __main__ as entry
    assert isinstance(entry.app, typer.Typer)
    assert callable(entry.main)
