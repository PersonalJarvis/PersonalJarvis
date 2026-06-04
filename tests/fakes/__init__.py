"""Test-Fakes — drop-in Implementierungen für Plugin-Protocols.

Konvention (CLAUDE.md): "Fakes statt Mocks". Pro Plugin-Protocol existiert
mindestens ein FakeXxxProvider/FakeXxxProcess mit scripted Responses; Tests
arbeiten gegen die Fakes statt gegen ``unittest.mock``.
"""
