"""The readback may paraphrase freely, but it may not rename the pane.

Both live failures this pins have the same shape: the deterministic result
named one pane, the voice named the pane the USER had asked about, and nothing
downstream compared the two. What matters as much as catching that is NOT
catching ordinary paraphrase — a check that cried wolf would be turned off
within a day, so the negative cases outnumber the positive ones here.
"""
from __future__ import annotations

import pytest

from jarvis.realtime.readback_check import swapped_call_signs

ROSTER = ["T1", "T2", "T3", "T4", "T6", "T8"]


class TestTheLiveFailures:
    def test_the_2026_08_13_readback_is_caught(self) -> None:
        """Result opened T5; the voice claimed the pane the user had named."""
        assert swapped_call_signs(
            "T5 ist offen. Ich briefe ihn gleich.",  # i18n-allow: quoted live result
            "Alles klar, ich habe T1 den Auftrag erteilt.",  # i18n-allow: live transcript
            roster=[*ROSTER, "T5"],
        ) == ("T1",)

    def test_the_2026_08_12_readback_is_caught(self) -> None:
        """Result opened T5 and T6; the voice named T2."""
        assert swapped_call_signs(
            "2 neue Terminals: T5, T6.",  # i18n-allow: quoted live result
            "Ich habe T2 angewiesen, das zu übernehmen.",  # i18n-allow: quoted live transcript
            roster=[*ROSTER, "T5"],
        ) == ("T2",)

    def test_a_spoken_position_is_folded_before_comparing(self) -> None:
        """"Terminal eins" is T1 — a swap said in words is still a swap."""
        assert swapped_call_signs(
            "T3 ist offen.",  # i18n-allow: quoted result
            "Ich habe Terminal eins gebrieft.",  # i18n-allow: quoted transcript
            roster=ROSTER,
        ) == ("T1",)


class TestOrdinaryRenderingIsLeftAlone:
    @pytest.mark.parametrize(
        ("result", "rendering"),
        [
            # The same pane, reworded — the whole point of the rendering order.
            ("T3 ist offen.", "Alles klar, T3 läuft jetzt."),  # i18n-allow: quoted
            # Named plus prose around it.
            ("T3 ist offen.", "Ich habe T3 gebrieft."),  # i18n-allow: quoted
            # No pane named at all: a legitimate paraphrase, not a claim.
            ("T3 ist offen.", "Das neue Terminal läuft."),  # i18n-allow: quoted
            # Every pane of a multi-pane result, in a different order.
            ("2 neue Terminals: T5, T6.", "T6 und T5 sind offen."),  # i18n-allow: quoted
            # A subset — saying less is not saying something false.
            ("2 neue Terminals: T5, T6.", "T5 ist offen."),  # i18n-allow: quoted
            # The result is about no pane at all, so nothing can contradict it.
            ("Alles gespeichert.", "Ich habe T1 gespeichert."),  # i18n-allow: quoted
            # A pane name inside a longer word must not match.
            ("T3 ist offen.", "Das T3000-Modul."),  # i18n-allow: quoted
        ],
    )
    def test_no_correction_is_invented(self, result: str, rendering: str) -> None:
        assert swapped_call_signs(result, rendering, roster=ROSTER) == ()


class TestItFailsOpen:
    @pytest.mark.parametrize(
        ("result", "rendering", "roster"),
        [
            ("", "Ich habe T1 gebrieft.", ROSTER),
            ("T3 ist offen.", "", ROSTER),
            ("T3 ist offen.", "Ich habe T1 gebrieft.", []),
            ("T3 ist offen.", "Ich habe T1 gebrieft.", ["", "  "]),
        ],
    )
    def test_missing_inputs_report_nothing(
        self, result: str, rendering: str, roster: list[str]
    ) -> None:
        assert swapped_call_signs(result, rendering, roster=roster) == ()

    def test_a_broken_canonicaliser_reports_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fault in the check must never invent a spoken correction."""
        from jarvis.agentic_ide import names

        def _boom(*_args: object, **_kwargs: object) -> str:
            raise RuntimeError("canonicaliser is down")

        monkeypatch.setattr(names, "canonical_positions", _boom)
        assert swapped_call_signs(
            "T3 ist offen.", "Ich habe T1 gebrieft.", roster=ROSTER
        ) == ()


def test_a_custom_pane_name_is_compared_too() -> None:
    """Panes may carry any label; the check must not be positions-only."""
    assert swapped_call_signs(
        "Nova ist offen.", "Ich habe Mika gebrieft.", roster=["Mika", "Nova"]
    ) == ("Mika",)


class TestTheSessionWiring:
    """The check is only worth anything if the turn boundary actually calls it.

    Driven through the real method with a stand-in ``self``: building a whole
    live session to observe one observation would pin the scaffolding rather
    than the behaviour, and the method touches exactly these four attributes.
    """

    @staticmethod
    def _session(roster: tuple[str, ...], published: list[tuple[str, str]]):
        from types import SimpleNamespace

        async def _publish_error(error_type: str, message: str, *, recoverable: bool):
            assert recoverable is True
            published.append((error_type, message))

        return SimpleNamespace(
            session_id="test-session",
            _workspace_call_signs=lambda: roster,
            _publish_error=_publish_error,
        )

    async def test_a_swapped_readback_is_published(self) -> None:
        from jarvis.realtime.session import RealtimeVoiceSession

        published: list[tuple[str, str]] = []
        session = self._session(("T1", "T5"), published)
        delegate = type("_State", (), {"last_reply": "T5 ist offen."})()
        await RealtimeVoiceSession._check_readback_fidelity(
            session,
            "Alles klar, ich habe T1 den Auftrag erteilt.",  # i18n-allow: quoted
            delegate,
            None,
        )
        assert len(published) == 1
        error_type, message = published[0]
        assert error_type == "readback_identifier_swap"
        assert "T1" in message

    async def test_a_faithful_readback_publishes_nothing(self) -> None:
        from jarvis.realtime.session import RealtimeVoiceSession

        published: list[tuple[str, str]] = []
        session = self._session(("T1", "T5"), published)
        delegate = type("_State", (), {"last_reply": "T5 ist offen."})()
        await RealtimeVoiceSession._check_readback_fidelity(
            session, "Alles klar, T5 läuft jetzt.", delegate, None  # i18n-allow: quoted
        )
        assert published == []

    async def test_a_broken_session_never_raises(self) -> None:
        """The observation must not be able to end a live call."""
        from jarvis.realtime.session import RealtimeVoiceSession

        class _Exploding:
            session_id = "test-session"

            @staticmethod
            def _workspace_call_signs():
                raise RuntimeError("registry is down")

        await RealtimeVoiceSession._check_readback_fidelity(
            _Exploding(), "Ich habe T1 gebrieft.", None, None
        )
