"""Reading how much of each subscription's plan is already spent.

What these pin down is not "does the JSON parse" but the handful of ways a
usage meter can be confidently wrong, which is worse than having none at all:

* a CACHED reading must never be reported as a live one — it is the number a
  user picks a subscription on, and for an idle seat it can be days old;
* a per-model weekly budget must survive alongside the overall weekly one,
  because 55% overall next to 99% on one model is the case where "plenty left"
  is the wrong conclusion;
* which of Codex's two windows is the short one is decided by its LENGTH, not
  by the slot it arrives in;
* a CLI with no reader must get an honest "unsupported", never a neighbouring
  provider's numbers;
* nothing here ever reaches the network in a test, and nothing ever returns a
  token.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from jarvis import agent_usage
from jarvis.agent_accounts import AgentAccount

#: Captured before any fixture runs. The autouse ``_no_network`` fixture below
#: replaces the module attribute, so a test that wants to exercise the REAL
#: transport helper cannot read it back off the module — it would get the stub.
_REAL_HTTP_GET_JSON = agent_usage._http_get_json


@pytest.fixture(autouse=True)
def _no_cache_between_tests() -> None:
    agent_usage.clear_cache()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every live probe fails unless a test opts in.

    A unit test that quietly reaches the real provider is a test that passes on
    the maintainer's machine and hangs in CI, so the default is "no answer" and
    the disk fallbacks are what get exercised.
    """
    monkeypatch.setattr(agent_usage, "_http_get_json", lambda url, headers: None)


def _account(platform: str, config_dir: Path, *, builtin: bool = False) -> AgentAccount:
    return AgentAccount(
        id=f"{platform}:test",
        platform=platform,
        label="Test seat",
        config_dir=config_dir,
        builtin=builtin,
    )


# ----------------------------------------------------------------- claude


CLAUDE_LIMITS_PAYLOAD = {
    "five_hour": {"utilization": 2.0, "resets_at": "2026-08-13T13:20:00+00:00"},
    "seven_day": {"utilization": 74.0, "resets_at": "2026-08-14T06:00:00+00:00"},
    "limits": [
        {
            "kind": "session",
            "group": "session",
            "percent": 2,
            "severity": "normal",
            "resets_at": "2026-08-13T13:20:00+00:00",
            "scope": None,
        },
        {
            "kind": "weekly_all",
            "group": "weekly",
            "percent": 74,
            "severity": "normal",
            "resets_at": "2026-08-14T06:00:00+00:00",
            "scope": None,
        },
        {
            "kind": "weekly_scoped",
            "group": "weekly",
            "percent": 100,
            "severity": "critical",
            "resets_at": "2026-08-14T06:00:00+00:00",
            "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
        },
    ],
}


def test_claude_keeps_the_scoped_weekly_budget_next_to_the_overall_one() -> None:
    """A model at 100% must not be hidden behind an overall week at 74%."""
    windows = agent_usage._claude_windows(CLAUDE_LIMITS_PAYLOAD)
    kinds = [w.kind for w in windows]
    assert kinds == ["session", "weekly", "weekly_scoped"]
    scoped = windows[2]
    assert scoped.percent == 100
    assert scoped.severity == "critical"
    assert scoped.scope_label == "Fable"


def test_claude_uses_the_providers_own_severity_not_its_own_arithmetic() -> None:
    """74% is 'normal' because the provider said so, not because 74 < 80."""
    payload = {
        "limits": [
            {"kind": "weekly_all", "group": "weekly", "percent": 12, "severity": "critical"},
        ]
    }
    (window,) = agent_usage._claude_windows(payload)
    assert window.severity == "critical"


def test_claude_falls_back_to_the_named_fields_when_there_is_no_limits_array() -> None:
    """An older payload shape still shows both headline numbers."""
    payload = {
        "five_hour": {"utilization": 10.0, "resets_at": "2026-08-13T13:20:00+00:00"},
        "seven_day": {"utilization": 90.0, "resets_at": "2026-08-14T06:00:00+00:00"},
    }
    windows = agent_usage._claude_windows(payload)
    assert [w.kind for w in windows] == ["session", "weekly"]
    assert windows[0].window_minutes == 300
    assert windows[1].window_minutes == 10080
    # No severity is stated in this shape, so the shared scale decides.
    assert windows[1].severity == "warning"


def test_claude_reports_a_disk_reading_as_cached_with_the_providers_timestamp(
    tmp_path: Path,
) -> None:
    """The whole honesty contract: an old number must not look like a live one."""
    (tmp_path / ".claude.json").write_text(
        json.dumps(
            {
                "oauthAccount": {"organizationRateLimitTier": "default_claude_max_20x"},
                "cachedUsageUtilization": {
                    "fetchedAtMs": 1786557552405,
                    "utilization": CLAUDE_LIMITS_PAYLOAD,
                },
            }
        ),
        encoding="utf-8",
    )
    usage = agent_usage.read_usage(_account("claude", tmp_path))
    assert usage.status == "ok"
    assert usage.source == "cached"
    assert usage.as_of == pytest.approx(1786557552.405)
    assert usage.plan == "Max 20x"
    assert len(usage.windows) == 3


def test_claude_without_any_login_says_signed_out_rather_than_zero_percent(
    tmp_path: Path,
) -> None:
    """An empty seat must not draw a reassuring 0% bar."""
    usage = agent_usage.read_usage(_account("claude", tmp_path))
    assert usage.status == "signed_out"
    assert usage.windows == ()


def test_an_unmapped_plan_tier_is_still_shown_readably(tmp_path: Path) -> None:
    (tmp_path / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"organizationRateLimitTier": "default_claude_max_50x"}}),
        encoding="utf-8",
    )
    usage = agent_usage.read_usage(_account("claude", tmp_path))
    assert usage.plan == "Max 50X"


# ------------------------------------------------------------------ codex


def _codex_signed_in(config_dir: Path) -> None:
    (config_dir / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "not-a-real-token", "account_id": "acct"}}),
        encoding="utf-8",
    )


def test_codex_names_its_windows_by_length_not_by_slot() -> None:
    """'primary' is not always the weekly one — the window length decides."""
    windows = agent_usage._codex_rate_limit_windows(
        {
            "primary": {"used_percent": 40.0, "window_minutes": 10080, "resets_at": 1786977114},
            "secondary": {"used_percent": 8.0, "window_minutes": 300, "resets_at": 1786900000},
        }
    )
    # Sorted shortest window first, which is also the display order.
    assert [w.kind for w in windows] == ["session", "weekly"]
    assert windows[0].percent == 8.0
    assert windows[1].percent == 40.0
    assert windows[0].resets_at is not None and windows[0].resets_at.endswith("+00:00")


def test_codex_recognises_a_thirty_day_window_as_monthly() -> None:
    """Measured on a real seat: 43200 minutes must not read as 'other'."""
    (window,) = agent_usage._codex_rate_limit_windows(
        {"primary": {"used_percent": 70.0, "window_minutes": 43200, "resets_at": 1789000000}}
    )
    assert window.kind == "monthly"


def test_codex_reads_the_newest_rate_limits_out_of_a_session_transcript(
    tmp_path: Path,
) -> None:
    """The disk fallback that carries this feature when the endpoint cannot."""
    _codex_signed_in(tmp_path)
    sessions = tmp_path / "sessions" / "2026" / "08" / "13"
    sessions.mkdir(parents=True)
    rollout = sessions / "rollout-2026-08-13T10-00-00-abc.jsonl"
    lines = [
        json.dumps({"timestamp": "2026-08-13T09:00:00.000Z", "type": "event_msg", "payload": {}}),
        json.dumps(
            {
                "timestamp": "2026-08-13T09:30:00.000Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {"used_percent": 11.0, "window_minutes": 10080},
                    },
                },
            }
        ),
        json.dumps(
            {
                "timestamp": "2026-08-13T10:00:00.000Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "plan_type": "plus",
                        "primary": {"used_percent": 23.0, "window_minutes": 10080},
                    },
                },
            }
        ),
    ]
    rollout.write_text("\n".join(lines) + "\n", encoding="utf-8")

    usage = agent_usage.read_usage(_account("codex", tmp_path))
    assert usage.status == "ok"
    assert usage.source == "cached"
    assert usage.plan == "Plus"
    # The LAST record wins — an earlier one in the same file is strictly older.
    assert [w.percent for w in usage.windows] == [23.0]


def test_codex_finds_the_record_in_a_transcript_larger_than_the_tail_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the tail is read, so the seek must not cut the answer off."""
    monkeypatch.setattr(agent_usage, "ROLLOUT_TAIL_BYTES", 2048)
    _codex_signed_in(tmp_path)
    sessions = tmp_path / "sessions"
    sessions.mkdir(parents=True)
    rollout = sessions / "rollout-big.jsonl"
    padding = [json.dumps({"type": "noise", "payload": {"blob": "x" * 200}}) for _ in range(200)]
    answer = json.dumps(
        {
            "timestamp": "2026-08-13T10:00:00.000Z",
            "payload": {"rate_limits": {"primary": {"used_percent": 5.0, "window_minutes": 300}}},
        }
    )
    rollout.write_text("\n".join([*padding, answer]) + "\n", encoding="utf-8")

    usage = agent_usage.read_usage(_account("codex", tmp_path))
    assert usage.status == "ok"
    assert [w.kind for w in usage.windows] == ["session"]


def test_codex_on_an_api_key_says_there_is_no_plan_limit(tmp_path: Path) -> None:
    """Per-request billing has no window to spend down — an empty bar would lie."""
    (tmp_path / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-test"}), encoding="utf-8")
    usage = agent_usage.read_usage(_account("codex", tmp_path))
    assert usage.status == "unsupported"


def test_codex_signed_in_but_never_used_is_unavailable_not_zero(tmp_path: Path) -> None:
    _codex_signed_in(tmp_path)
    usage = agent_usage.read_usage(_account("codex", tmp_path))
    assert usage.status == "unavailable"
    assert usage.windows == ()


# ---------------------------------------------------------------- generic


def test_an_unknown_cli_gets_an_honest_answer_not_a_neighbours_numbers(
    tmp_path: Path,
) -> None:
    """The failure an `if claude / else codex` pair would produce silently."""
    (tmp_path / ".claude.json").write_text(
        json.dumps({"cachedUsageUtilization": {"utilization": CLAUDE_LIMITS_PAYLOAD}}),
        encoding="utf-8",
    )
    usage = agent_usage.read_usage(_account("some-new-cli", tmp_path))
    assert usage.status == "unsupported"
    assert usage.windows == ()


def test_supports_usage_reports_only_the_clis_with_a_real_reader() -> None:
    assert agent_usage.supports_usage("claude") is True
    assert agent_usage.supports_usage("codex") is True
    assert agent_usage.supports_usage("some-new-cli") is False


# ------------------------------------------------------------- collect/cache


def test_collect_serves_the_cache_until_the_ttl_expires(tmp_path: Path) -> None:
    """The panel polls; a percentage does not move fast enough to re-ask each time."""
    calls: list[str] = []
    account = _account("claude", tmp_path)

    def counting_reader(acc: AgentAccount) -> agent_usage.AccountUsage:
        calls.append(acc.id)
        return agent_usage.AccountUsage(account_id=acc.id, platform=acc.platform, status="ok")

    original = agent_usage._READERS["claude"]
    agent_usage._READERS["claude"] = counting_reader
    try:
        agent_usage.collect([account])
        agent_usage.collect([account])
        assert calls == [account.id]
        # A forced refresh is what the manual button does, and it must bypass it.
        agent_usage.collect([account], refresh=True)
        assert len(calls) == 2
        # A zero TTL means "never reuse", which is how the tests above stay honest.
        agent_usage.collect([account], ttl=0)
        assert len(calls) == 3
    finally:
        agent_usage._READERS["claude"] = original


def test_a_reader_that_raises_costs_one_row_not_the_panel(tmp_path: Path) -> None:
    account = _account("claude", tmp_path)

    def exploding_reader(_acc: AgentAccount) -> agent_usage.AccountUsage:
        raise RuntimeError("provider changed everything")

    original = agent_usage._READERS["claude"]
    agent_usage._READERS["claude"] = exploding_reader
    try:
        usage = agent_usage.read_usage(account)
    finally:
        agent_usage._READERS["claude"] = original
    assert usage.status == "unavailable"


def test_no_reading_ever_carries_a_credential(tmp_path: Path) -> None:
    """The wire shape is display-only by construction."""
    (tmp_path / ".claude.json").write_text(
        json.dumps({"cachedUsageUtilization": {"utilization": CLAUDE_LIMITS_PAYLOAD}}),
        encoding="utf-8",
    )
    (tmp_path / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "sk-ant-oat-secret",
                    "refreshToken": "refresh-secret",
                    "expiresAt": 1,
                }
            }
        ),
        encoding="utf-8",
    )
    payload = json.dumps(agent_usage.read_usage(_account("claude", tmp_path)).to_dict())
    assert "secret" not in payload
    assert "sk-ant" not in payload


# ------------------------------------------------------------------ shared


@pytest.mark.parametrize(
    ("percent", "expected"),
    [(0.0, "normal"), (79.9, "normal"), (80.0, "warning"), (94.9, "warning"), (95.0, "critical")],
)
def test_one_severity_scale_serves_every_provider(percent: float, expected: str) -> None:
    """Two rows at the same percentage must never be coloured differently."""
    assert agent_usage._severity(percent) == expected


def test_an_unreachable_host_is_left_alone_but_an_http_error_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An offline machine must not pay a six-second timeout on every poll.

    The asymmetry is the point: being unreachable is expensive and stays true
    for a while, so it earns a cooldown. An HTTP status answers instantly, so
    retrying it costs nothing — and cooling THAT down would keep a seat on
    cached numbers for minutes after a one-off server hiccup.
    """
    monkeypatch.setattr(agent_usage, "_http_get_json", _REAL_HTTP_GET_JSON)
    agent_usage.clear_cache()

    attempts: list[str] = []

    class _Unreachable:
        def get(self, url: str, **_kw: object) -> object:
            attempts.append(url)
            raise OSError("no route to host")

    class _Rejecting:
        def get(self, url: str, **_kw: object) -> object:
            attempts.append(url)

            class _Response:
                status_code = 403

                def json(self) -> object:
                    return {}

            return _Response()

    monkeypatch.setitem(sys.modules, "httpx", _Unreachable())
    assert agent_usage._http_get_json("https://example.invalid/u", {}) is None
    assert agent_usage._http_get_json("https://example.invalid/u", {}) is None
    assert attempts == ["https://example.invalid/u"], "the second probe must be skipped"

    attempts.clear()
    agent_usage.clear_cache()
    monkeypatch.setitem(sys.modules, "httpx", _Rejecting())
    assert agent_usage._http_get_json("https://example.invalid/v", {}) is None
    assert agent_usage._http_get_json("https://example.invalid/v", {}) is None
    assert len(attempts) == 2, "a rejected request must still be retried"


def test_an_over_consumed_window_is_clamped_to_full_rather_than_dropped() -> None:
    """103% means 'full'; refusing to draw it would hide the worst state."""
    assert agent_usage._clamp_percent(103) == 100.0
    assert agent_usage._clamp_percent(-5) == 0.0
    assert agent_usage._clamp_percent("nope") is None
    assert agent_usage._clamp_percent(True) is None
