"""Drop feedback survives the shared-strip replacement."""

from jarvis.ui.jarvisbar import renderer as R


def test_drop_feedback_is_visible_and_distinct():
    frames = [
        R.JarvisBarRenderer().render(0, "idle", drop_state=state).tobytes()
        for state in R.DROP_STATES
    ]
    assert len(set(frames)) == len(R.DROP_STATES)


def test_confirmation_expires():
    painter = R.JarvisBarRenderer()
    idle = painter.render(0, "idle").tobytes()
    for state in (R.DROP_STATE_OK, R.DROP_STATE_REJECTED):
        assert (
            painter.render(
                0, "idle", drop_state=state, drop_elapsed=R.DROP_CONFIRM_TOTAL_S
            ).tobytes()
            == idle
        )
