"""Tests for the deterministic config readback (Wave 1.4).

In the voice "apply everything now" path there is no pre-confirmation, so the
post-change spoken line is the only source of truth. It must therefore be
deterministic and FAITHFUL to the real pipeline outcome — never a free-form
"done" the brain made up. config_readback renders that line in de/en/es from the
set_config_value tool result.
"""
from __future__ import annotations

from uuid import uuid4

# Preload config before self_mod to satisfy the pre-existing import order
# (writer→config→brain→voice→echo_confirmation cycles back onto self_mod).
import jarvis.core.config  # noqa: F401, E402  isort:skip
from jarvis.core.self_mod import PendingMutation  # noqa: E402
from jarvis.voice.config_readback import config_readback  # noqa: E402


def _applied_dump(
    *, description: str, new_value: object, requires_restart: bool = False,
    path: str = "tts.speed",
) -> dict:
    return PendingMutation(
        id=uuid4(),
        path=path,
        old_value=1.0,
        new_value=new_value,
        needs_confirmation=False,
        risk_tier="ask",
        requires_restart=requires_restart,
        applied=True,
        description=description,
    ).model_dump(mode="json")


class TestSuccess:
    def test_applied_speaks_the_real_value(self) -> None:
        out = _applied_dump(description="TTS speed", new_value=1.25)
        text = config_readback(success=True, output=out, language="en")
        assert "1.25" in text  # honest: the value actually written
        assert "done" in text.lower()

    def test_applied_restart_mentions_restart(self) -> None:
        out = _applied_dump(description="STT provider", new_value="groq",
                            requires_restart=True)
        text = config_readback(success=True, output=out, language="en")
        assert "restart" in text.lower()

    def test_spanish_is_supported(self) -> None:
        out = _applied_dump(description="TTS speed", new_value=1.25)
        text = config_readback(success=True, output=out, language="es")
        assert text is not None
        assert "done" not in text.lower()  # not English


class TestHonestFailures:
    def test_forbidden_is_an_honest_refusal_not_done(self) -> None:
        out = {"error_kind": "forbidden_path", "path": "security.admin_password_hash"}
        text = config_readback(success=False, output=out, language="en")
        assert text is not None
        # The crucial guarantee: a blocked change is NEVER read back as success.
        assert "done" not in text.lower()
        # And it must not leak the path.
        assert "security" not in text.lower()

    def test_rollback_never_says_done(self) -> None:
        out = {"error_kind": "reload_failed_rolled_back", "path": "tts.speed"}
        text = config_readback(success=False, output=out, language="de")
        assert text is not None
        assert "erledigt" not in text.lower()  # never falsely "done"

    def test_invalid_value_never_says_done(self) -> None:
        out = {"error_kind": "validate_failed", "path": "tts.speed"}
        text = config_readback(success=False, output=out, language="en")
        assert text is not None
        assert "done" not in text.lower()


class TestFallthrough:
    def test_non_config_result_returns_none(self) -> None:
        # A result that isn't a recognizable config outcome → caller keeps its
        # normal (free-form brain) phrasing.
        assert config_readback(success=True, output={"foo": "bar"}, language="en") is None

    def test_non_dict_output_returns_none(self) -> None:
        assert config_readback(success=True, output="hello", language="en") is None
