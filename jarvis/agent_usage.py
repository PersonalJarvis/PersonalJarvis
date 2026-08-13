"""How much of each connected subscription is already spent.

A user who holds several seats of the same coding CLI
(:mod:`jarvis.agent_accounts`) can switch which one the next terminal opens
against — but until now the one number that decides WHICH seat to pick was
invisible here. The plan limits (Claude's rolling 5-hour window and its 7-day
window, Codex's weekly window) were only ever readable by starting the CLI and
typing its own status command, once per seat, in a pane that then had to be
thrown away. This module answers the same question for every registered seat at
once, without opening anything.

**Per account, never "this machine".** The reader is handed ONE account
directory and reports on exactly that login, the same narrow question
:func:`jarvis.agent_accounts.describe` asks. A cross-directory search would be
actively misleading here: it would paint one seat's remaining budget onto a
neighbouring row, and the whole point of the feature is deciding between rows.

**Live first, cached second, and it always says which.** Both CLIs cache their
last known limits on disk, and that cache is genuinely useful — it survives an
expired token and needs no network. It is also, for an idle seat, arbitrarily
old: a weekly window that reads 40% may have been written four days ago. So a
reading carries its ``source`` and its ``as_of``, and the UI states them.
Presenting a stale number as a live one is the single worst outcome here,
because it is the number the user is about to make a decision on.

**Nothing is ever spawned and no token ever leaves.** Usage is one small HTTPS
GET against the provider the account is already signed in to, plus a few file
reads. The bearer is read from the account's own credential file, used for that
one request, and never returned, never logged, never written anywhere.

Cross-platform by construction: ``pathlib`` throughout, no OS branch, and every
failure path — no network, no login, an unknown CLI, a provider that changed its
payload — degrades to an honest "not available" with a reason rather than to a
confident wrong percentage.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from jarvis.agent_accounts import AgentAccount

#: How long a reading is reused before the provider is asked again. The panel
#: polls, several seats are read at once, and a plan percentage simply does not
#: move fast enough to justify a request per render. Deliberately short enough
#: that a user watching a running agent still sees the number climb.
USAGE_TTL_S: float = 60.0

#: One provider request must never hold the panel. Six seconds is far past a
#: healthy response and far short of the point where the user assumes a hang;
#: a timeout degrades to the on-disk snapshot, which is the whole reason it
#: exists.
HTTP_TIMEOUT_S: float = 6.0

#: Newest session transcripts scanned when Codex has no live answer. A busy
#: CODEX_HOME holds thousands; the limits in all but the newest few are strictly
#: older, so scanning further is pure I/O for a worse number.
MAX_ROLLOUT_FILES: int = 12

#: Bytes read from the END of a transcript. The rate-limit record is written on
#: every turn, so the last one is always near the tail — reading a 200 MB
#: transcript to find a line 300 bytes from its end is how a "cheap" status read
#: becomes a disk stall.
ROLLOUT_TAIL_BYTES: int = 512 * 1024

#: Where a percentage stops being routine. Shared by every provider so two rows
#: at the same percentage can never be coloured differently — Claude states its
#: own severity, Codex states none, and inventing a second scale for the second
#: provider is how that inconsistency would arrive.
WARNING_PERCENT: float = 80.0
CRITICAL_PERCENT: float = 95.0

#: Windows shorter than this count as the rolling "session" limit rather than a
#: weekly one. Claude's is 5 hours, Codex's short window is 5 hours; anything up
#: to a day is unambiguously the short one.
SESSION_WINDOW_MAX_MINUTES: int = 24 * 60

#: A 7-day window, in minutes. Codex reports windows numerically, so this is
#: what "weekly" is compared against, with a day of slack either side.
WEEKLY_WINDOW_MINUTES: int = 7 * 24 * 60

#: A 30-day window, in minutes. Measured, not assumed: a ChatGPT seat on this
#: machine reports a 43200-minute window, and without a name for it the panel
#: would have labelled a real monthly budget "other".
MONTHLY_WINDOW_MINUTES: int = 30 * 24 * 60


class _Cache:
    """TTL cache of readings, keyed by account id and its directory.

    The directory is part of the key on purpose: an account that is deleted and
    re-added under the same label gets a new directory, and serving the previous
    one's percentages for a minute would be a wrong answer that looks right.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], tuple[float, AccountUsage]] = {}

    def get(self, account: AgentAccount, ttl: float) -> AccountUsage | None:
        key = (account.id, os.path.normcase(str(account.config_dir)))
        with self._lock:
            hit = self._entries.get(key)
        if hit is None:
            return None
        stored_at, usage = hit
        return usage if (time.time() - stored_at) < ttl else None

    def put(self, account: AgentAccount, usage: AccountUsage) -> None:
        key = (account.id, os.path.normcase(str(account.config_dir)))
        with self._lock:
            self._entries[key] = (time.time(), usage)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


@dataclass(frozen=True, slots=True)
class UsageWindow:
    """One plan limit, normalised across providers.

    ``kind`` is the vocabulary the UI translates against and is deliberately
    small and closed: ``session`` (the rolling short window), ``weekly``,
    ``weekly_scoped`` (a weekly budget restricted to one model or surface), and
    ``other`` for anything a provider adds that this build has no name for. An
    unrecognised limit is still SHOWN — with its provider label — because a
    limit the user is being throttled by must never be the one the panel hides.
    """

    kind: str
    percent: float
    severity: str  # "normal" | "warning" | "critical"
    #: ISO-8601 UTC. Providers disagree (Claude sends a timestamp, Codex an
    #: epoch), so both are converted here rather than in the browser.
    resets_at: str | None = None
    #: Length of the window, when the provider states it. Lets the UI write
    #: "5 h" without inferring it from the kind.
    window_minutes: int | None = None
    #: What the budget is restricted to, e.g. a model name. Only ever set for
    #: ``weekly_scoped``; the UI appends it to the translated label.
    scope_label: str | None = None
    #: The provider's own wording, kept for ``other`` kinds where this build has
    #: no translation to offer.
    raw_label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "percent": self.percent,
            "severity": self.severity,
            "resets_at": self.resets_at,
            "window_minutes": self.window_minutes,
            "scope_label": self.scope_label,
            "raw_label": self.raw_label,
        }


@dataclass(frozen=True, slots=True)
class AccountUsage:
    """Everything the panel needs to draw one account's remaining budget.

    ``status`` separates the four outcomes that must not look alike:
    ``ok`` (windows carry real numbers), ``signed_out`` (nothing to measure —
    the row already says so), ``unsupported`` (this CLI publishes no usage this
    build can read) and ``unavailable`` (it should have worked and did not,
    with ``message`` naming why).
    """

    account_id: str
    platform: str
    status: str
    windows: tuple[UsageWindow, ...] = ()
    #: "live" | "cached" — which of the two paths produced these numbers.
    source: str = "live"
    #: Epoch seconds the numbers were true at. For a cached reading this is the
    #: provider's own timestamp, which is the entire reason it is reported.
    as_of: float | None = None
    message: str = ""
    #: Display-only plan name, e.g. "Max 20x". Never used for a decision.
    plan: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "platform": self.platform,
            "status": self.status,
            "windows": [w.to_dict() for w in self.windows],
            "source": self.source,
            "as_of": self.as_of,
            "message": self.message,
            "plan": self.plan,
        }


_CACHE = _Cache()


# ------------------------------------------------------------------ helpers


def clear_cache() -> None:
    """Drop every cached reading and any unreachable-host cooldown."""
    _CACHE.clear()
    _UNREACHABLE_UNTIL.clear()


def _severity(percent: float) -> str:
    """One scale for every provider — see :data:`WARNING_PERCENT`."""
    if percent >= CRITICAL_PERCENT:
        return "critical"
    if percent >= WARNING_PERCENT:
        return "warning"
    return "normal"


def _clamp_percent(value: Any) -> float | None:
    """A usable 0-100 percentage, or ``None`` for anything that is not one.

    Clamped rather than rejected at the top end: a provider that reports 103%
    for an over-consumed window means "full", and refusing to draw that bar
    would hide the one state the user most needs to see.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return max(0.0, min(100.0, number))


def _iso_from_epoch(value: Any) -> str | None:
    """Codex's epoch-seconds reset time as ISO-8601 UTC."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
    except (OSError, OverflowError, ValueError) as exc:
        # A nonsense timestamp costs the countdown, never the percentage.
        logger.debug("Agent usage: unusable reset epoch {!r} ({})", value, exc)
        return None


def _iso_or_none(value: Any) -> str | None:
    """Claude's already-ISO reset time, validated rather than trusted."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        logger.debug("Agent usage: unusable reset timestamp {!r} ({})", text, exc)
        return None
    return text


def _kind_for_window(window_minutes: int | None) -> str:
    """Name a provider's numeric window in the shared vocabulary."""
    if window_minutes is None or window_minutes <= 0:
        return "other"
    if window_minutes <= SESSION_WINDOW_MAX_MINUTES:
        return "session"
    if abs(window_minutes - WEEKLY_WINDOW_MINUTES) <= 24 * 60:
        return "weekly"
    if abs(window_minutes - MONTHLY_WINDOW_MINUTES) <= 3 * 24 * 60:
        return "monthly"
    return "other"


#: How long a host that could not be REACHED at all is left alone.
#:
#: Only unreachability triggers it, never an HTTP error. The distinction is the
#: whole value: an offline machine pays the full timeout on every poll — six
#: seconds a minute, per seat, forever — while a 403 answers instantly and
#: costs nothing worth avoiding. Cooling down on status codes as well would
#: keep a seat on cached numbers for minutes after a one-off server hiccup, and
#: freshness is what this feature is for.
_UNREACHABLE_COOLDOWN_S: float = 180.0

#: ``{url: epoch until which it is not worth trying}``. Process-local and
#: deliberately unsynchronised: a duplicate probe during a race costs one
#: request, and a lock here would serialise every seat's read.
_UNREACHABLE_UNTIL: dict[str, float] = {}


def _http_get_json(url: str, headers: dict[str, str]) -> Any | None:
    """One short GET, or ``None`` for every failure. Never raises, never logs a token.

    The import is local because this module is reached from status paths that
    must stay cheap on a host with no network at all; ``httpx`` is a real
    dependency, but paying its import to read a cached percentage is waste.
    """
    blocked_until = _UNREACHABLE_UNTIL.get(url)
    if blocked_until is not None and time.time() < blocked_until:
        return None
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - httpx is a hard dependency
        logger.debug("Agent usage: httpx is unavailable ({})", exc)
        return None
    try:
        response = httpx.get(url, headers=headers, timeout=HTTP_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - every transport failure is "no live answer"
        _UNREACHABLE_UNTIL[url] = time.time() + _UNREACHABLE_COOLDOWN_S
        logger.debug("Agent usage: {} could not be reached ({})", url, type(exc).__name__)
        return None
    _UNREACHABLE_UNTIL.pop(url, None)
    if response.status_code != 200:
        logger.debug("Agent usage: {} answered {}", url, response.status_code)
        return None
    try:
        return response.json()
    except ValueError as exc:
        logger.debug("Agent usage: {} did not answer with JSON ({})", url, exc)
        return None


def _read_json_file(path: Path) -> Any | None:
    """A JSON file as plain Python values, or ``None`` when it is not usable."""
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        # Absent is the normal state for a seat that has never been used.
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        logger.debug("Agent usage: {} is not valid JSON ({})", path.name, exc)
        return None


# ------------------------------------------------------------------- claude


#: The endpoint Claude Code itself reads its usage from. An OAuth bearer is the
#: only credential it accepts, which is exactly what a signed-in seat holds.
_CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

#: Required by the OAuth surface; without it the bearer is rejected as if it
#: were an API key.
_CLAUDE_OAUTH_BETA = "oauth-2025-04-20"

#: Rate-limit tiers as the account file spells them, mapped to what the plan is
#: called on the invoice. Unknown tiers fall through to a tidied version of the
#: raw string rather than to nothing — a new plan name must still show up.
_CLAUDE_PLAN_NAMES: dict[str, str] = {
    "default_claude_max_20x": "Max 20x",
    "default_claude_max_5x": "Max 5x",
    "default_claude_pro": "Pro",
    "default_claude_team": "Team",
    "default_claude_enterprise": "Enterprise",
}

#: How long each Claude window is. The endpoint states the reset time but not
#: the length, and the UI needs the length to write "5 h" beside the bar.
_CLAUDE_WINDOW_MINUTES: dict[str, int] = {
    "session": 5 * 60,
    "weekly": WEEKLY_WINDOW_MINUTES,
    "weekly_scoped": WEEKLY_WINDOW_MINUTES,
}

#: The named per-window fields, in display order, used only when the payload
#: carries no ``limits`` array. Older builds of the endpoint answered with these
#: alone, and a seat on one must still show its two headline numbers.
_CLAUDE_LEGACY_WINDOWS: tuple[tuple[str, str, int | None], ...] = (
    ("five_hour", "session", 5 * 60),
    ("seven_day", "weekly", WEEKLY_WINDOW_MINUTES),
)


def _claude_plan_name(claude_json: Any) -> str | None:
    account = claude_json.get("oauthAccount") if isinstance(claude_json, dict) else None
    tier = account.get("organizationRateLimitTier") if isinstance(account, dict) else None
    if not isinstance(tier, str) or not tier.strip():
        return None
    known = _CLAUDE_PLAN_NAMES.get(tier.strip())
    if known:
        return known
    # An unmapped tier is still worth showing; strip the prefix every tier
    # carries so a new one reads as "Max 50x" rather than as a database key.
    cleaned = tier.strip().removeprefix("default_claude_").replace("_", " ").strip()
    return cleaned.title() if cleaned else None


def _claude_scope_label(scope: Any) -> str | None:
    """What a scoped weekly budget is restricted to, e.g. the model name."""
    if not isinstance(scope, dict):
        return None
    model = scope.get("model")
    if isinstance(model, dict):
        name = model.get("display_name") or model.get("id")
        if isinstance(name, str) and name.strip():
            return name.strip()
    surface = scope.get("surface")
    if isinstance(surface, str) and surface.strip():
        return surface.strip()
    return None


def _claude_windows(utilization: Any) -> tuple[UsageWindow, ...]:
    """Normalise one Anthropic usage payload into windows.

    Reads the ``limits`` array when it is there and the named fields when it is
    not. The array is preferred because it is self-describing: it carries the
    per-model weekly budgets that have no named field at all, so a seat
    throttled on one model while its overall week sits at 40% shows BOTH, which
    is the difference between "you have plenty left" and the truth.
    """
    if not isinstance(utilization, dict):
        return ()

    entries = utilization.get("limits")
    windows: list[UsageWindow] = []
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            percent = _clamp_percent(entry.get("percent"))
            if percent is None:
                continue
            group = entry.get("group")
            kind_raw = entry.get("kind")
            scope_label = _claude_scope_label(entry.get("scope"))
            if kind_raw == "session" or group == "session":
                kind = "session"
            elif scope_label:
                kind = "weekly_scoped"
            elif group == "weekly":
                kind = "weekly"
            else:
                kind = "other"
            severity = entry.get("severity")
            windows.append(
                UsageWindow(
                    kind=kind,
                    percent=percent,
                    # The provider's own severity wins where it is stated: it
                    # knows about grace periods and soft caps that a bare
                    # percentage does not express.
                    severity=(
                        severity
                        if severity in ("normal", "warning", "critical")
                        else _severity(percent)
                    ),
                    resets_at=_iso_or_none(entry.get("resets_at")),
                    window_minutes=_CLAUDE_WINDOW_MINUTES.get(kind),
                    scope_label=scope_label,
                    raw_label=kind_raw if isinstance(kind_raw, str) else None,
                )
            )
    if windows:
        return tuple(windows)

    for field, kind, window_minutes in _CLAUDE_LEGACY_WINDOWS:
        block = utilization.get(field)
        if not isinstance(block, dict):
            continue
        percent = _clamp_percent(block.get("utilization"))
        if percent is None:
            continue
        windows.append(
            UsageWindow(
                kind=kind,
                percent=percent,
                severity=_severity(percent),
                resets_at=_iso_or_none(block.get("resets_at")),
                window_minutes=window_minutes,
            )
        )
    return tuple(windows)


def _claude_usage(account: AgentAccount) -> AccountUsage:
    """Usage for ONE Claude seat: the live endpoint, else its own disk cache."""
    from jarvis.claude_credentials import claude_login_in

    claude_json = _read_json_file(account.config_dir / ".claude.json")
    plan = _claude_plan_name(claude_json)
    login = claude_login_in(account.config_dir)

    if login.status == "valid" and login.access_token:
        payload = _http_get_json(
            _CLAUDE_USAGE_URL,
            {
                "Authorization": f"Bearer {login.access_token}",
                "anthropic-beta": _CLAUDE_OAUTH_BETA,
                "Accept": "application/json",
            },
        )
        windows = _claude_windows(payload)
        if windows:
            return AccountUsage(
                account_id=account.id,
                platform=account.platform,
                status="ok",
                windows=windows,
                source="live",
                as_of=time.time(),
                plan=plan,
            )

    # Claude Code writes its last reading here, so an expired bearer, a
    # firewalled host or a changed endpoint still produces the numbers the CLI
    # itself last saw — labelled with the provider's own timestamp so nobody
    # mistakes a four-day-old weekly figure for a live one.
    cached = claude_json.get("cachedUsageUtilization") if isinstance(claude_json, dict) else None
    if isinstance(cached, dict):
        windows = _claude_windows(cached.get("utilization"))
        fetched_ms = cached.get("fetchedAtMs")
        if windows:
            return AccountUsage(
                account_id=account.id,
                platform=account.platform,
                status="ok",
                windows=windows,
                source="cached",
                as_of=(
                    float(fetched_ms) / 1000.0
                    if isinstance(fetched_ms, (int, float)) and not isinstance(fetched_ms, bool)
                    else None
                ),
                plan=plan,
            )

    if login.status == "absent":
        return AccountUsage(
            account_id=account.id,
            platform=account.platform,
            status="signed_out",
            message="Sign in to this seat to see how much of its plan is left.",
            plan=plan,
        )
    return AccountUsage(
        account_id=account.id,
        platform=account.platform,
        status="unavailable",
        message=(
            "Usage could not be read for this seat. It appears again by itself "
            "once a terminal has run on this account, or when the connection is back."
        ),
        plan=plan,
    )


# -------------------------------------------------------------------- codex


#: Where a ChatGPT-backed Codex seat publishes its remaining budget. Tried
#: first and allowed to fail: the transcript fallback below is the path this
#: build has actually measured, so a 404 here costs freshness, never the
#: feature.
_CODEX_USAGE_URLS: tuple[str, ...] = ("https://chatgpt.com/backend-api/codex/usage",)


def _codex_rate_limit_windows(rate_limits: Any) -> tuple[UsageWindow, ...]:
    """Normalise Codex's ``primary``/``secondary`` pair into windows.

    Which of the two is the short window and which is the weekly one is NOT
    fixed — it depends on the plan, and hardcoding "primary is weekly" was
    tempting because it is true for the account this was written against. The
    window length decides instead, so a plan whose windows are the other way
    round labels them correctly.
    """
    if not isinstance(rate_limits, dict):
        return ()
    windows: list[UsageWindow] = []
    for slot in ("primary", "secondary"):
        block = rate_limits.get(slot)
        if not isinstance(block, dict):
            continue
        percent = _clamp_percent(block.get("used_percent"))
        if percent is None:
            continue
        raw_minutes = block.get("window_minutes")
        window_minutes = (
            int(raw_minutes)
            if isinstance(raw_minutes, (int, float)) and not isinstance(raw_minutes, bool)
            else None
        )
        resets = _iso_from_epoch(block.get("resets_at"))
        if resets is None:
            # Older Codex builds state a countdown instead of a timestamp.
            seconds = block.get("resets_in_seconds")
            if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
                resets = _iso_from_epoch(time.time() + float(seconds))
        windows.append(
            UsageWindow(
                kind=_kind_for_window(window_minutes),
                percent=percent,
                severity=_severity(percent),
                resets_at=resets,
                window_minutes=window_minutes,
            )
        )
    windows.sort(key=lambda w: w.window_minutes or 0)
    return tuple(windows)


def _codex_rollout_files(codex_home: Path) -> list[Path]:
    """The newest session transcripts, newest first, bounded by count.

    ``os.scandir`` rather than ``rglob`` because a long-lived CODEX_HOME holds
    one file per session in a date tree, and stat-ing every one of thousands to
    sort them is the expensive part of this whole module.
    """
    sessions = codex_home / "sessions"
    found: list[tuple[float, Path]] = []
    stack: list[Path] = [sessions]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir():
                            stack.append(Path(entry.path))
                        elif entry.name.startswith("rollout-") and entry.name.endswith(".jsonl"):
                            found.append((entry.stat().st_mtime, Path(entry.path)))
                    except OSError:  # noqa: PERF203 - one unreadable entry is simply skipped
                        continue
        except OSError:
            # An absent sessions tree means this seat has never run. Not a fault.
            continue
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [path for _mtime, path in found[:MAX_ROLLOUT_FILES]]


def _codex_last_rate_limits(path: Path) -> tuple[dict[str, Any], float] | None:
    """The final rate-limit record in one transcript, plus its timestamp."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > ROLLOUT_TAIL_BYTES:
                handle.seek(size - ROLLOUT_TAIL_BYTES)
                handle.readline()  # Drop the partial line the seek landed in.
            tail = handle.read().decode("utf-8", "replace")
    except OSError as exc:
        logger.debug("Agent usage: {} could not be read ({})", path.name, exc)
        return None
    for line in reversed(tail.splitlines()):
        if '"rate_limits"' not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:  # noqa: PERF203 - a truncated line is simply not the answer
            continue
        payload = record.get("payload") if isinstance(record, dict) else None
        limits = payload.get("rate_limits") if isinstance(payload, dict) else None
        if not isinstance(limits, dict):
            continue
        stamp = record.get("timestamp") if isinstance(record, dict) else None
        as_of = path.stat().st_mtime
        if isinstance(stamp, str):
            try:
                as_of = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
            except ValueError:
                # Keep the file's mtime — it is a worse answer, not a wrong one.
                pass
        return limits, as_of
    return None


def _codex_plan_name(rate_limits: Any) -> str | None:
    if not isinstance(rate_limits, dict):
        return None
    for field in ("plan_type", "limit_name"):
        value = rate_limits.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip().replace("_", " ").title()
    return None


def _codex_usage(account: AgentAccount) -> AccountUsage:
    """Usage for ONE Codex seat: the live endpoint, else its own transcripts."""
    from jarvis.codex_auth import codex_login_in

    connected, mode, _email = codex_login_in(account.config_dir)
    if not connected:
        return AccountUsage(
            account_id=account.id,
            platform=account.platform,
            status="signed_out",
            message="Sign in to this seat to see how much of its plan is left.",
        )
    if mode == "api_key":
        # An API key is billed per call and has no plan window to spend down.
        # Drawing an empty 0% bar next to it would invent a limit that does not
        # exist; saying so is the honest answer.
        return AccountUsage(
            account_id=account.id,
            platform=account.platform,
            status="unsupported",
            message="This seat bills per request through an API key — it has no plan limit.",
        )

    auth = _read_json_file(account.config_dir / "auth.json")
    tokens = auth.get("tokens") if isinstance(auth, dict) else None
    token = tokens.get("access_token") if isinstance(tokens, dict) else None
    account_ref = None
    if isinstance(tokens, dict):
        account_ref = tokens.get("account_id")
    if not account_ref and isinstance(auth, dict):
        account_ref = auth.get("account_id")

    if isinstance(token, str) and token:
        for url in _CODEX_USAGE_URLS:
            payload = _http_get_json(
                url,
                {
                    "Authorization": f"Bearer {token}",
                    "chatgpt-account-id": str(account_ref or ""),
                    "Accept": "application/json",
                },
            )
            source = payload.get("rate_limits") if isinstance(payload, dict) else None
            windows = _codex_rate_limit_windows(source if source is not None else payload)
            if windows:
                return AccountUsage(
                    account_id=account.id,
                    platform=account.platform,
                    status="ok",
                    windows=windows,
                    source="live",
                    as_of=time.time(),
                    plan=_codex_plan_name(source if source is not None else payload),
                )

    # Codex writes the limits the server returned into its session transcript on
    # every turn, so the newest transcript holds the freshest reading this
    # machine has ever seen — which for a seat used today is minutes old.
    for path in _codex_rollout_files(account.config_dir):
        found = _codex_last_rate_limits(path)
        if found is None:
            continue
        rate_limits, as_of = found
        windows = _codex_rate_limit_windows(rate_limits)
        if windows:
            return AccountUsage(
                account_id=account.id,
                platform=account.platform,
                status="ok",
                windows=windows,
                source="cached",
                as_of=as_of,
                plan=_codex_plan_name(rate_limits),
            )

    return AccountUsage(
        account_id=account.id,
        platform=account.platform,
        status="unavailable",
        message=(
            "No usage recorded for this seat yet — it appears once a terminal "
            "has run on this account."
        ),
    )


# ------------------------------------------------------------------ generic


def _generic_usage(account: AgentAccount) -> AccountUsage:
    """The answer for a CLI this build has no usage reader for.

    Falling through to another provider's reader is what an ``if claude / else
    codex`` pair quietly does to every third CLI, and here it would be worse
    than a wrong dot: it would draw a plausible progress bar from a file that
    belongs to a different product. An honest "not available" is the only safe
    answer, and switching seats is unaffected by it.
    """
    from jarvis.workspace.agents import get_agent

    entry = get_agent(account.platform)
    name = entry.display_name if entry is not None else account.platform
    return AccountUsage(
        account_id=account.id,
        platform=account.platform,
        status="unsupported",
        message=f"{name} does not publish plan usage that Jarvis can read.",
    )


#: Which reader answers for which CLI. A mapping rather than a chain of ``if``s
#: so that adding a provider is one entry, and so that the absence of an entry
#: is a visible fact rather than an accidental fallthrough into a neighbour.
_READERS: dict[str, Any] = {
    "claude": _claude_usage,
    "codex": _codex_usage,
}


def supports_usage(platform: str) -> bool:
    """Whether this build can read plan usage for *platform* at all."""
    return platform in _READERS


def read_usage(account: AgentAccount) -> AccountUsage:
    """Usage for one account, reading the provider directly. Never raises.

    Uncached: callers that render a list want :func:`collect`, which adds the
    TTL and reads several seats at once.
    """
    reader = _READERS.get(account.platform, _generic_usage)
    try:
        return reader(account)
    except Exception as exc:  # noqa: BLE001 - a status read must never break the panel
        logger.warning(
            "Agent usage: {} could not be read for {!r}: {}: {}",
            account.platform,
            account.label,
            type(exc).__name__,
            exc,
        )
        return AccountUsage(
            account_id=account.id,
            platform=account.platform,
            status="unavailable",
            message="Usage could not be read for this seat.",
        )


def collect(
    accounts: list[AgentAccount],
    *,
    refresh: bool = False,
    ttl: float = USAGE_TTL_S,
) -> dict[str, AccountUsage]:
    """Usage for many accounts at once, keyed by account id.

    Read in PARALLEL, which is the difference between a panel that updates and
    one that stalls: each seat costs one network round trip, and four seats read
    one after another is four timeouts stacked end to end on a host with no
    connection. Cached seats never reach the pool at all.
    """
    results: dict[str, AccountUsage] = {}
    pending: list[AgentAccount] = []
    for account in accounts:
        hit = None if refresh else _CACHE.get(account, ttl)
        if hit is None:
            pending.append(account)
        else:
            results[account.id] = hit

    if pending:
        with ThreadPoolExecutor(
            max_workers=min(8, len(pending)), thread_name_prefix="agent-usage"
        ) as pool:
            fresh = list(pool.map(read_usage, pending))
        for account, usage in zip(pending, fresh, strict=True):
            _CACHE.put(account, usage)
            results[account.id] = usage
    return results


__all__ = [
    "CRITICAL_PERCENT",
    "HTTP_TIMEOUT_S",
    "MAX_ROLLOUT_FILES",
    "USAGE_TTL_S",
    "WARNING_PERCENT",
    "AccountUsage",
    "UsageWindow",
    "clear_cache",
    "collect",
    "read_usage",
    "supports_usage",
]
