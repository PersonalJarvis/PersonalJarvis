"""The parsed-TOML cache behind ``load_config``.

``load_config`` is called from over a hundred sites, several of them on the
event loop that also serves every WebSocket. Re-reading and re-parsing the
config there is what left the backend thread inside ``tomllib`` for over ten
minutes at 88 % of a core with ``/api/health`` timing out, the window titled
"Not responding", and keystrokes typed into an Agentic-IDE pane arriving
seconds late (measured 2026-07-28).

What the cache must never do is answer with something that was not on disk, so
these tests pin the two ways that could happen — a mutated payload leaking back
in, and a write going unnoticed — rather than only the happy path.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from jarvis.core import config as config_module
from jarvis.core.config import _load_toml, clear_config_cache


@pytest.fixture(autouse=True)
def _clean_cache():
    """No test may inherit or leave behind a cached payload."""
    clear_config_cache()
    yield
    clear_config_cache()


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_second_read_does_not_touch_the_parser(tmp_path, monkeypatch):
    """A repeat read is served from the cache, not by parsing again."""
    target = tmp_path / "jarvis.toml"
    _write(target, '[brain]\nprimary = "gemini"\n')

    assert _load_toml(target)["brain"]["primary"] == "gemini"

    calls: list[str] = []
    real_loads = config_module.tomllib.loads

    def counting_loads(text: str):
        calls.append(text)
        return real_loads(text)

    monkeypatch.setattr(config_module.tomllib, "loads", counting_loads)

    assert _load_toml(target)["brain"]["primary"] == "gemini"
    assert calls == [], "a cached payload must not be re-parsed"


def test_caller_mutations_never_reach_the_cache(tmp_path):
    """The cache hands out copies — ``_apply_env_overrides`` mutates in place.

    This is the failure that would corrupt the config silently rather than
    loudly: every env override ever applied would accumulate in the cached dict
    and be served to callers as if it had been read from the file.
    """
    target = tmp_path / "jarvis.toml"
    _write(target, '[brain]\nprimary = "gemini"\n\n[ui]\nadmin_api_port = 47821\n')

    first = _load_toml(target)
    first["brain"]["primary"] = "mutated-by-caller"
    first["brain"]["injected"] = True
    first["ui"] = {"replaced": True}

    second = _load_toml(target)
    assert second["brain"]["primary"] == "gemini"
    assert "injected" not in second["brain"]
    assert second["ui"]["admin_api_port"] == 47821


def test_nested_containers_are_copied_not_shared(tmp_path):
    """Copying has to reach all the way down, not just the top level."""
    target = tmp_path / "jarvis.toml"
    _write(
        target,
        "[brain.providers.gemini]\nbase_url = \"https://example.invalid\"\n"
        "[memory]\nnames = [\"a\", \"b\"]\n",
    )

    first = _load_toml(target)
    second = _load_toml(target)

    assert first["brain"]["providers"] is not second["brain"]["providers"]
    assert first["memory"]["names"] is not second["memory"]["names"]

    first["brain"]["providers"]["gemini"]["base_url"] = "https://tampered.invalid"
    first["memory"]["names"].append("c")

    third = _load_toml(target)
    assert third["brain"]["providers"]["gemini"]["base_url"] == "https://example.invalid"
    assert third["memory"]["names"] == ["a", "b"]


def test_a_changed_file_is_read_again(tmp_path):
    """Editing the config must take effect — the whole point of invalidation."""
    target = tmp_path / "jarvis.toml"
    _write(target, '[brain]\nprimary = "gemini"\n')
    assert _load_toml(target)["brain"]["primary"] == "gemini"

    _write(target, '[brain]\nprimary = "openrouter"\n')
    # Force a distinct identity even on a filesystem with a coarse clock, so
    # this test pins invalidation rather than the host's timestamp resolution.
    os.utime(target, ns=(0, 10_000_000_000))

    assert _load_toml(target)["brain"]["primary"] == "openrouter"


def test_a_same_size_rewrite_within_one_tick_is_caught_by_clear(tmp_path):
    """Identity can miss a rewrite; ``clear_config_cache`` is the backstop.

    A flag flip keeps the byte count, and a fast enough rewrite keeps the
    timestamp — which is precisely what ``config_writer._atomic_write``
    announces instead of leaving to chance.
    """
    target = tmp_path / "jarvis.toml"
    _write(target, "[ui]\nstart_hidden = true\n")
    assert _load_toml(target)["ui"]["start_hidden"] is True

    _write(target, "[ui]\nstart_hidden = fals\n".replace("fals", "false"))
    os.utime(target, ns=(0, 0))  # same identity as far as the cache can tell

    clear_config_cache()
    assert _load_toml(target)["ui"]["start_hidden"] is False


def test_an_unstattable_path_still_raises_its_real_error(tmp_path):
    """A missing file reports itself, rather than being masked by the cache."""
    with pytest.raises(OSError):
        _load_toml(tmp_path / "does-not-exist.toml")


def test_two_files_do_not_share_an_entry(tmp_path):
    """Keyed by path — a profile and the main config are not the same file."""
    one = tmp_path / "a.toml"
    other = tmp_path / "b.toml"
    _write(one, '[brain]\nprimary = "gemini"\n')
    _write(other, '[brain]\nprimary = "openrouter"\n')

    assert _load_toml(one)["brain"]["primary"] == "gemini"
    assert _load_toml(other)["brain"]["primary"] == "openrouter"
    assert _load_toml(one)["brain"]["primary"] == "gemini"


def test_writer_invalidates_the_cache(tmp_path):
    """A write through ``config_writer`` is visible to the next read."""
    from jarvis.core import config_writer

    target = tmp_path / "jarvis.toml"
    _write(target, '[brain]\nprimary = "gemini"\n')
    assert _load_toml(target)["brain"]["primary"] == "gemini"

    config_writer._atomic_write(target, '[brain]\nprimary = "openrouter"\n')

    assert _load_toml(target)["brain"]["primary"] == "openrouter"


def test_load_config_reflects_an_edit(tmp_path, monkeypatch):
    """End to end: the public entry point is not left holding a stale config.

    The env override is cleared deliberately. A developer machine really does
    export ``JARVIS__BRAIN__PRIMARY``, and env outranks the file by design — so
    leaving it in place would make this assert the precedence rule while
    claiming to assert the cache, and it would pass whether or not the cache
    ever invalidated.
    """
    from jarvis.core.config import load_config

    monkeypatch.delenv("JARVIS__BRAIN__PRIMARY", raising=False)

    target = tmp_path / "jarvis.toml"
    _write(target, '[brain]\nprimary = "gemini"\n')
    assert load_config(target).brain.primary == "gemini"

    _write(target, '[brain]\nprimary = "openrouter"\n')
    os.utime(target, ns=(0, 10_000_000_000))

    assert load_config(target).brain.primary == "openrouter"


def test_legacy_structured_stt_env_cannot_brick_config_load(tmp_path, monkeypatch):
    """A stale PowerShell object string must not replace the models table."""
    from jarvis.core.config import load_config

    monkeypatch.setenv(
        "JARVIS__STT__MODELS",
        "@{openrouter-stt=openai/gpt-4o-transcribe}",
    )
    target = tmp_path / "jarvis.toml"
    _write(
        target,
        '[stt.models]\nopenrouter-stt = "openai/gpt-4o-transcribe"\n',
    )

    cfg = load_config(target)

    assert cfg.stt.models == {"openrouter-stt": "openai/gpt-4o-transcribe"}
    assert "JARVIS__STT__MODELS" not in os.environ


def test_schema_blocks_legacy_mapping_env_when_toml_table_is_absent(
    tmp_path, monkeypatch
):
    """Minimal configs still get their structured shape from Pydantic."""
    from jarvis.core.config import load_config

    monkeypatch.setenv("JARVIS__STT__MODELS", "@{provider=model}")
    target = tmp_path / "jarvis.toml"
    _write(target, '[brain]\nprimary = "gemini"\n')

    cfg = load_config(target)

    assert cfg.stt.models == {}
    assert "JARVIS__STT__MODELS" not in os.environ


def test_scalar_env_cannot_replace_an_arbitrary_mapping(monkeypatch):
    """The safeguard follows the data shape instead of naming one STT key."""
    monkeypatch.setenv("JARVIS__EXAMPLE__MAPPING", "@{nested=value}")
    data = {"example": {"mapping": {"nested": "value"}}}

    result = config_module._apply_env_overrides(data)

    assert result["example"]["mapping"] == {"nested": "value"}
    assert "JARVIS__EXAMPLE__MAPPING" not in os.environ


def test_json_env_can_replace_an_arbitrary_mapping(monkeypatch):
    """Valid JSON remains a supported structured environment override."""
    monkeypatch.setenv("JARVIS__EXAMPLE__MAPPING", '{"nested":"updated"}')
    data = {"example": {"mapping": {"nested": "value"}}}

    result = config_module._apply_env_overrides(data)

    assert result["example"]["mapping"] == {"nested": "updated"}


# --------------------------------------------------------------------------
# Load-rate alarm
#
# The cache above removed the PARSE from a repeat load; it did not make a load
# free. Every call still pays the structural copy, the env pass and a full
# Pydantic validation — 0.51 ms measured 2026-08-14. That is small enough to
# look free at a call site and large enough that ~2000 calls/s saturate a core,
# and since the work holds the GIL it stops every other thread in the process:
# the same "Not responding" window this module's docstring describes, returned
# on 2026-08-14 with the burner inside `_copy_toml_data` instead of `tomllib`
# (28 of 30 stack samples). A caller looping on the config is therefore made to
# announce itself instead of being absorbed silently by a faster cache.
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_rate_state(tmp_path, monkeypatch):
    """Rate state is module-global; no test may inherit another's window.

    The report also APPENDS to ``data/config_pathology.log`` next to the real
    config so a wedged process can still deliver it. A test must never write
    there: it would bury the one live report this file exists to preserve.
    """
    monkeypatch.setattr(
        config_module, "resolve_config_path", lambda: tmp_path / "jarvis.toml"
    )
    config_module._load_rate_window_started = 0.0
    config_module._load_rate_count = 0
    config_module._load_rate_last_alert = 0.0
    yield
    config_module._load_rate_window_started = 0.0
    config_module._load_rate_count = 0
    config_module._load_rate_last_alert = 0.0


def test_the_pathology_report_never_writes_next_to_the_real_config(tmp_path):
    """Pinned because a stray test write buries the one live report we need."""
    written = tmp_path / "data" / "config_pathology.log"
    config_module._report_config_pathology("probe")

    assert written.exists(), "the report must land under the resolved config dir"
    assert "probe" in written.read_text(encoding="utf-8")


def test_ordinary_load_rate_stays_silent(caplog):
    """Normal use must never produce the alarm, or it teaches people to ignore it."""
    with caplog.at_level("WARNING", logger="jarvis.core.config"):
        for _ in range(config_module._LOAD_RATE_ALERT - 1):
            config_module._note_config_load()

    assert "config load storm" not in caplog.text


def test_a_storming_caller_is_named_once(caplog):
    """The alarm fires on a pathological rate and points at the calling frame."""
    with caplog.at_level("WARNING", logger="jarvis.core.config"):
        for _ in range(config_module._LOAD_RATE_ALERT + 50):
            config_module._note_config_load()

    storms = [r for r in caplog.records if "config load storm" in r.getMessage()]
    assert len(storms) == 1, "one burst must produce exactly one report"
    # The stack has to reach the actual caller, which is this test.
    assert "test_config_toml_cache.py" in storms[0].getMessage()


def test_a_continuing_storm_does_not_flood_the_log(caplog):
    """A wedged caller must not turn one defect into thousands of log lines."""
    with caplog.at_level("WARNING", logger="jarvis.core.config"):
        for _ in range(config_module._LOAD_RATE_ALERT * 5):
            config_module._note_config_load()

    storms = [r for r in caplog.records if "config load storm" in r.getMessage()]
    assert len(storms) == 1, "the cooldown must hold while the storm continues"


def test_load_config_counts_through_the_public_entry_point(tmp_path, monkeypatch):
    """The counter sits in load_config itself, not only in the helper."""
    target = tmp_path / "jarvis.toml"
    _write(target, '[brain]\nprimary = "gemini"\n')

    before = config_module._load_rate_count
    config_module.load_config(config_file=target)

    assert config_module._load_rate_count != before
