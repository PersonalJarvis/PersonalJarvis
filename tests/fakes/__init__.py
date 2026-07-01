"""Test fakes — drop-in implementations for plugin protocols.

Convention (CLAUDE.md): "fakes over mocks". Every plugin protocol has
at least one FakeXxxProvider/FakeXxxProcess with scripted responses; tests
work against the fakes instead of ``unittest.mock``.
"""
