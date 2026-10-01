"""A timed-out cold build must not immediately start a second model load."""

from jarvis.dictation import local_final, local_preview


def test_failed_cold_build_starts_cooldown_at_failure(monkeypatch):
    clock = [100.0]
    attempts = []

    def failed_spawn(*args, **kwargs):
        attempts.append(clock[0])
        clock[0] += 180.0
        return None

    monkeypatch.setattr(local_final.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(local_preview, "faster_whisper_available", lambda: True)
    monkeypatch.setattr(local_preview, "_spawn_worker_model", failed_spawn)
    provider = local_final.LocalFinalSTT(allow_cpu=True)
    provider.warm_up()
    assert not provider.is_warm
    assert provider._ensure_worker(blocking=False) is None
    assert len(attempts) == 1
    clock[0] += local_final._RETRY_AFTER_S + 1
    provider.warm_up()
    assert len(attempts) == 2
