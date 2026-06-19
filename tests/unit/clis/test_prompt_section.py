"""CONNECTED CLIS prompt section renderer (design 2026-06-10 §5.3)."""
from tests.unit.clis._fakes import FakeCliRegistry, FakeTool, make_spec


def test_renders_connected_cli_with_description_and_examples():
    from jarvis.clis.prompt_section import render_connected_clis_section

    fake = FakeCliRegistry({"gh": make_spec("gh")}, active=[FakeTool("cli_gh")])
    section = render_connected_clis_section(fake)
    assert "CONNECTED CLIS" in section
    assert "cli_gh" in section
    assert "gh test capability." in section  # decl description preferred
    assert "`gh list`" in section  # example commands rendered
    assert "Answer ONLY from the tool result" in section


def test_empty_when_nothing_connected():
    from jarvis.clis.prompt_section import render_connected_clis_section

    fake = FakeCliRegistry({"gh": make_spec("gh")}, active=[])
    assert render_connected_clis_section(fake) == ""


def test_falls_back_to_spec_description_without_capabilities():
    from dataclasses import replace

    from jarvis.clis.prompt_section import render_connected_clis_section

    spec = replace(make_spec("gh"), capabilities=())
    fake = FakeCliRegistry({"gh": spec}, active=[FakeTool("cli_gh")])
    assert "gh CLI." in render_connected_clis_section(fake)


def test_defensive_on_broken_registry():
    from jarvis.clis.prompt_section import render_connected_clis_section

    class _Broken:
        def active_tools(self):
            raise RuntimeError("boom")

    assert render_connected_clis_section(_Broken()) == ""


def test_section_prefers_cli_over_plugin():
    from jarvis.clis.prompt_section import render_connected_clis_section

    fake = FakeCliRegistry({"gh": make_spec("gh")}, active=[FakeTool("cli_gh")])
    out = render_connected_clis_section(fake)
    assert "plugin" in out.lower()  # explicit CLI-over-plugin wording


def test_section_tells_model_to_self_discover_with_help():
    from jarvis.clis.prompt_section import render_connected_clis_section

    fake = FakeCliRegistry({"gh": make_spec("gh")}, active=[FakeTool("cli_gh")])
    out = render_connected_clis_section(fake)
    assert "--help" in out
