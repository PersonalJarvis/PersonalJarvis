import pytest

from jarvis.realtime.protocol import RealtimeProvider


def _load_provider_class():
    from jarvis.plugins.realtime.openai_realtime import OpenAIRealtimeProvider
    return OpenAIRealtimeProvider


def test_provider_is_structurally_conformant():
    cls = _load_provider_class()
    inst = cls()
    assert isinstance(inst, RealtimeProvider)
    assert inst.supports_realtime is True
    assert inst.name == "openai-realtime"


@pytest.mark.asyncio
async def test_can_open_duplex_session_returns_bool_when_keyless(monkeypatch):
    import jarvis.plugins.realtime.openai_realtime as mod

    monkeypatch.setattr(mod, "get_provider_secret", lambda _p: None)
    inst = _load_provider_class()()
    assert await inst.can_open_duplex_session() is False


def test_module_does_not_import_openai_at_top_level():
    # AP-26: the SDK import is lazy inside methods, not at module import.
    import ast
    import pathlib

    src = pathlib.Path("jarvis/plugins/realtime/openai_realtime.py").read_text("utf-8")
    tree = ast.parse(src)
    top_imports = [
        n
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for n in (getattr(node, "names", []) or [])
    ]
    assert not any("openai" in (a.name or "") for a in top_imports)
