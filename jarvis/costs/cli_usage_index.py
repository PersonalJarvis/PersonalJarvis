"""Incremental index of token usage in the vendor coding-CLI session logs.

The cost read model (:mod:`jarvis.costs.sources`) answers a request in
milliseconds because every store it reads is a small SQLite file. The coding
CLIs are the opposite: they append JSONL transcripts that on a working machine
reach several gigabytes (measured here: 659 MB of Claude Code across 948 files,
9.5 GB of Codex across 2466 files, one single rollout of 484 MB). Parsing that
per request is impossible, so this module reads each file ONCE, remembers the
byte offset it got to, and writes one small row per model call into its own
database. Callers then query rows, never logs.

Nothing here prices anything — that stays with the read model. This module only
answers *how many tokens, when, by which agent and model*.

What each source looks like — MEASURED on a live install, not taken from
documentation:

**Claude Code** — ``<config>/projects/<slug>/<session>.jsonl``. The records
that carry usage have ``type == "assistant"`` and hold it at
``message.usage``, with the thinking count nested one level deeper in
``usage.output_tokens_details``.

*The identity of such a record is NOT its ``uuid``.* One API response is
written as one line PER content block, each line getting its own ``uuid`` while
repeating the SAME ``message.usage``. On a sampled transcript that was 64
assistant lines for 31 responses — deduplicating by ``uuid`` would have
reported roughly twice the tokens that were actually billed. The identity used
here is ``message.id`` (the ``msg_...`` the API assigns), falling back to
``requestId`` and only then to ``uuid``. Because that id is unique everywhere,
the same response copied into a resumed or forked transcript is also counted
once, which is the correct answer.

**Codex** — ``<home>/sessions/<YYYY>/<MM>/<DD>/rollout-<iso>-<uuid>.jsonl``.
Usage arrives as ``event_msg`` records with ``payload.type == "token_count"``.
Only ``payload.info.last_token_usage`` is read: its sibling
``total_token_usage`` is CUMULATIVE, so summing it would multiply the real
number by the turn count. ``info`` is ``null`` on the records a session emits
before its first model call — those contribute nothing. The model is not on the
usage record; it is on ``turn_context`` records and is carried forward within
the file (and across a resumed scan, which is why the resume row stores it).
The session id comes from the ``session_meta`` record, or from the filename.

Two Codex conventions differ from Claude's and both cost real money when
missed (a 37 000 USD "bill" on one machine, 2026-08-24):

* ``input_tokens`` INCLUDES ``cached_input_tokens`` — the OpenAI usage
  object reports cache hits as a breakdown of the input count, whereas
  Anthropic reports them as a separate field. The cached share is subtracted
  so ``tokens_in`` means "uncached input" for every agent alike.
* A forked or resumed thread starts a NEW rollout file and replays its whole
  parent history into it as fresh ``token_count`` records — thousands of
  them, stamped with the moment of the fork. Byte offsets are new, so they
  cannot identify a turn across files. The running total can: within one
  lineage it is monotonic and unique per real model call, and the lineage is
  what ``session_meta.session_id`` names (a fork keeps its parent's
  ``session_id`` and gets its own ``id``). The dedup key is therefore
  ``session_id + total_token_usage`` and the replay collapses onto the
  original. A replayed row can arrive BEFORE the parent's ``turn_context``
  and so carry no model; the insert lets a later row with a model fill it in.

**agy** — ``<root>/sessions/**/wire.jsonl``. Two generations ship under one
binary and keep separate roots (``~/.kimi``, ``~/.kimi-code``); both are read,
since a machine can carry both histories. The usage record shape is taken from
real transcripts of the LEGACY layout, the only one on this machine that holds
finished turns::

    {"timestamp": 1770293705.85, "message": {"type": "StatusUpdate", "payload":
      {"token_usage": {"input_other": 1384, "output": 184,
                       "input_cache_read": 4864, "input_cache_creation": 0}}}}

Those counters are per model call, not cumulative (verified: they rise and fall
across a session). The eleven current-layout transcripts available here contain
only the setup prefix a session writes when it opens, so that generation's
finished-turn shape is UNVERIFIED: its files are scanned with the same reader
and contribute nothing unless they carry the same records. Nothing is guessed.
agy records no model id anywhere in the transcript, so :attr:`CliTurn.model`
stays empty for it.

Design rules this module holds itself to:

* **Never raises.** A missing root, an unreadable file, a corrupt line, a
  locked database — each degrades to "contributed nothing" and is logged.
  A fresh machine with none of these directories returns empty results.
* **Incremental.** A file is re-opened at its stored offset when it grew, and
  re-read from zero only when it SHRANK (rotated or rewritten), whose rows are
  dropped first. An offset is only ever stored at a line boundary.
* **Bounded.** :func:`refresh` stops when its deadline is spent, commits what
  it has and says ``complete=False``. Files are taken newest-modified first and
  no single file may take more than :data:`_FILE_BYTE_BUDGET` per run, so one
  half-gigabyte rollout cannot starve the rest.
* **Cheap first.** Every line is tested for a substring on the raw bytes before
  ``json.loads`` is considered.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any
from urllib.parse import quote

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

AGENT_CLAUDE = "claude-cli"
"""Claude Code. Same spelling as ``jarvis.costs.model.SUBSCRIPTION_RUNNERS``."""

AGENT_CODEX = "codex-cli"
"""The Codex CLI."""

AGENT_KIMI = "kimi-cli"
"""Kimi Code. Both generations keep wire logs under ``~/.kimi`` /
``~/.kimi-code``. Historically stored under the ``agy-cli`` name; renamed
when Antigravity (the Google ``agy`` binary) gained its own reader so the
two no longer share a Spend row."""

AGENT_AGY = "agy-cli"
"""Antigravity (``agy``). Conversations live as SQLite files under
``~/.gemini/antigravity-cli/conversations/<uuid>.db``. Token counts are
inside the protobuf ``gen_metadata`` blobs (measured on agy 1.1.20); the
model id is a string field on the same blob (``gemini-3.7-flash``, …)."""

AGENT_GROK = "grok-cli"
"""Grok Build (``~/.grok``). ``sessions/<url-encoded cwd>/<uuid>/updates.jsonl``
carries one ``turn_completed`` record per turn with a usage block that is
per-turn (verified against three turn_started/turn_completed pairs) and
follows the OpenAI convention — ``inputTokens`` INCLUDES ``cachedReadTokens``.
``prompt_id`` identifies the turn wherever it is written. The model is the
single key of ``usage.modelUsage``; cwd and session id come from the folder's
``summary.json``. 608 MB of these were read by nothing until 2026-08-25."""

AGENT_OPENCODE = "opencode-cli"
"""OpenCode. Everything lives in one SQLite store,
``~/.local/share/opencode/opencode.db`` (``$XDG_DATA_HOME/opencode`` when
set): ``message.data`` is JSON with ``role``, ``modelID``, ``providerID``,
``tokens.{input,output,reasoning,cache.{read,write}}`` and — because the
user brings their own key — the ``cost`` OpenCode itself computed. Read
with a timestamp cursor instead of a byte offset; the message id is the
identity. Not a subscription: the recorded cost is the bill."""

AGENTS: tuple[str, ...] = (
    AGENT_CLAUDE,
    AGENT_CODEX,
    AGENT_KIMI,
    AGENT_AGY,
    AGENT_GROK,
    AGENT_OPENCODE,
)

#: Every coding harness the workspace registry can open, mapped to the
#: index reader that counts its spend. A harness whose transcripts cannot be
#: read from disk is listed in ``HARNESSES_WITHOUT_LOCAL_TRANSCRIPT`` with the
#: reason. ``tests/unit/costs/test_harness_cost_parity.py`` fails the build
#: when a registry entry is in neither — so a new harness is not shippable
#: until its cost accounting exists (maintainer mandate, 2026-08-25).
COST_READER_FOR_HARNESS: dict[str, str] = {
    "claude": AGENT_CLAUDE,
    "codex": AGENT_CODEX,
    "kimi": AGENT_KIMI,
    "antigravity": AGENT_AGY,
    "grok-build": AGENT_GROK,
    "opencode": AGENT_OPENCODE,
    # GLM runs the Claude Code binary against z.ai with the same config
    # directory, so its sessions land in ~/.claude and are read — and priced —
    # as Claude Code. Attributing them to z.ai needs a config dir of their own
    # at spawn (docs/BUGS.md BUG-178, still open).
    "glm": AGENT_CLAUDE,
}
HARNESSES_WITHOUT_LOCAL_TRANSCRIPT: dict[str, str] = {
    "deepseek-harness": (
        "dsh keeps no usage log on disk (checked ~/.dsh, ~/.deepseek, %LOCALAPPDATA%/dsh)"
    ),
}

#: Filename of the index, relative to the data dir.
DB_NAME = "cli_usage_index.db"

# Token accounting, mirrored from ``jarvis/costs/sources.py`` so both halves of
# the section count the same way. Cache CREATION is input (billed at a premium),
# cache READS are their own cheap bucket.
_IN_KEYS = (
    "input_tokens",
    "prompt_tokens",
    "cache_creation_input_tokens",
    "cache_write_input_tokens",
)
_OUT_KEYS = ("output_tokens", "completion_tokens")

# Reasoning is a BREAKDOWN of the output count, never a sibling of it. It only
# stands in when a source reports no primary output count at all.
_OUT_DETAIL_KEYS = ("reasoning_output_tokens", "thinking_tokens")

_CACHED_KEYS = (
    "cache_read_input_tokens",
    "cached_input_tokens",
    "cache_read_tokens",
    "cache_hit_tokens",
)

# agy names its counters after the position they occupy in a request rather
# than after the API field they came from. Renaming them into the canonical
# vocabulary above is a translation, not a second accounting rule.
_AGY_KEY_MAP = {
    "input_other": "input_tokens",
    "input_cache_creation": "cache_creation_input_tokens",
    "input_cache_read": "cache_read_input_tokens",
    "output": "output_tokens",
}

# Cheap pre-filters, tested on the raw bytes of a line before any parsing.
_CLAUDE_MARK = b'"usage"'
_CODEX_MARKS = (b'"token_count"', b'"turn_context"', b'"session_meta"')
_AGY_MARK = b'"token_usage"'
_GROK_MARK = b'"turn_completed"'

_LABEL_MAX = 90
_DB_TIMEOUT_S = 5.0

#: How many lines pass between two clock reads. Reading the clock per line of a
#: 484 MB file costs more than the parsing it guards.
_DEADLINE_CHECK_LINES = 2000

#: Most bytes one file may contribute to a single :func:`refresh`. Without it a
#: single huge rollout would consume every run's whole budget forever and the
#: files behind it would never be reached.
_FILE_BYTE_BUDGET = 48 * 1024 * 1024

#: The trailing UUID of ``rollout-<iso>-<uuid>.jsonl``.
_ROLLOUT_ID = re.compile(r"([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$")

# Bumped whenever a reader's accounting changes so rows already indexed under
# the old rule would be wrong. An older index is dropped and rebuilt from the
# transcripts, which are the source of truth; nothing is lost but time.
#   2 — Codex: cached input subtracted from input; lineage-based dedup.
#   3 — cost_usd column (OpenCode records its own price). Additive: migrated
#       in place, no rebuild.
#   4 — kimi wire logs were stored as ``agy-cli``; Antigravity (the Google
#       ``agy`` binary) has its own conversation DBs. Rebuild so the two no
#       longer share a Spend row.
_SCHEMA_VERSION = 4
#: Versions whose rows are still right and only need columns added.
_MIGRATE_IN_PLACE_FROM = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS indexed_files (
    path        TEXT PRIMARY KEY,
    agent       TEXT NOT NULL,
    session_id  TEXT NOT NULL DEFAULT '',
    size        INTEGER NOT NULL DEFAULT 0,
    mtime_ns    INTEGER NOT NULL DEFAULT 0,
    byte_offset INTEGER NOT NULL DEFAULT 0,
    model       TEXT NOT NULL DEFAULT '',
    cwd         TEXT NOT NULL DEFAULT '',
    label       TEXT NOT NULL DEFAULT '',
    scanned_ms  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cli_turns (
    agent         TEXT NOT NULL,
    dedup_key     TEXT NOT NULL,
    path          TEXT NOT NULL DEFAULT '',
    session_id    TEXT NOT NULL DEFAULT '',
    ts_ms         INTEGER NOT NULL DEFAULT 0,
    model         TEXT NOT NULL DEFAULT '',
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    tokens_cached INTEGER NOT NULL DEFAULT 0,
    cwd           TEXT NOT NULL DEFAULT '',
    label         TEXT NOT NULL DEFAULT '',
    cost_usd      REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (agent, dedup_key)
);
CREATE INDEX IF NOT EXISTS idx_cli_turns_ts ON cli_turns (ts_ms);
CREATE INDEX IF NOT EXISTS idx_cli_turns_session ON cli_turns (agent, session_id);
"""

# A duplicate is ignored except for one field: a row indexed without a model
# (a Codex replay ahead of its ``turn_context``) takes the model from the copy
# that knows it. Nothing else may change — the first sighting is the record.
_INSERT_TURN = (
    "INSERT INTO cli_turns "
    "(agent, dedup_key, path, session_id, ts_ms, model, "
    " tokens_in, tokens_out, tokens_cached, cwd, label, cost_usd) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(agent, dedup_key) DO UPDATE SET model=excluded.model "
    "WHERE cli_turns.model = '' AND excluded.model <> ''"
)

_UPSERT_FILE = (
    "INSERT INTO indexed_files "
    "(path, agent, session_id, size, mtime_ns, byte_offset, model, cwd, label, scanned_ms) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(path) DO UPDATE SET "
    "agent=excluded.agent, session_id=excluded.session_id, size=excluded.size, "
    "mtime_ns=excluded.mtime_ns, byte_offset=excluded.byte_offset, model=excluded.model, "
    "cwd=excluded.cwd, label=excluded.label, scanned_ms=excluded.scanned_ms"
)


# ---------------------------------------------------------------------------
# Public shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CliTurn:
    """One model call a coding CLI made, as its own transcript recorded it.

    ``model`` is empty where the CLI does not write one down (Kimi's wire
    log), and ``cwd`` is empty where its layout does not preserve it
    (Kimi's legacy per-folder buckets are an MD5 of the directory).
    """

    agent: str
    session_id: str
    ts_ms: int
    model: str
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    cwd: str
    label: str
    #: What the CLI itself priced the call at, when it does (OpenCode). 0.0
    #: for the seat-driven CLIs, whose bill is derived from the rate tables.
    cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class CliRollup:
    """Indexed turns summed inside one time bucket.

    The cost report groups by model, session and time bucket and never shows
    an individual call, so summing them in SQLite costs it nothing and saves
    it building six figures' worth of objects per request.
    """

    agent: str
    session_id: str
    ts_ms: int
    """Start of the bucket the rows fell into."""

    model: str
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    turns: int
    cwd: str
    label: str
    cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class RefreshResult:
    """What one :func:`refresh` managed to do inside its budget."""

    files_seen: int
    """Transcripts discovered on disk, whether or not they needed reading."""

    files_scanned: int
    """Transcripts actually opened and read from this run."""

    bytes_read: int
    turns_added: int

    complete: bool
    """False when the deadline or a per-file cap cut the run short. Whatever
    was read is committed either way; the next call resumes where it stopped."""

    elapsed_s: float
    errors: int
    """Files that could not be read or parsed at all. Logged, never raised."""


@dataclass(frozen=True, slots=True)
class IndexState:
    """How far the index has got — enough for a UI to say "still indexing"."""

    files_known: int
    files_indexed: int
    files_pending: int
    bytes_pending: int
    turns: int
    db_path: Path

    @property
    def complete(self) -> bool:
        return self.files_pending == 0


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _int(value: object) -> int:
    try:
        return int(value or 0)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        # Silence is right: one malformed counter in a historic transcript means
        # that counter is 0, not that the whole file stops being indexable. The
        # turn still carries its timestamp, session and model.
        return 0


def _clip(text: object, limit: int = _LABEL_MAX) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _iso_ms(value: object) -> int:
    """ISO-8601 (``...Z`` included) to epoch milliseconds, 0 when unusable."""
    if not isinstance(value, str) or not value:
        return 0
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError as exc:
        log.debug("cli usage index: unparsable timestamp %r (%s)", value, exc)
        return 0
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return int(stamp.timestamp() * 1000)


def _epoch_ms(value: object) -> int:
    """Epoch seconds (agy) or milliseconds to epoch milliseconds."""
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        log.debug("cli usage index: unusable epoch stamp %r", value)
        return 0
    if seconds <= 0:
        return 0
    # A value already in milliseconds is far past any plausible second-count.
    if seconds > 1e11:
        return int(seconds)
    return int(seconds * 1000)


def _usage_totals(usage: Mapping[str, Any]) -> tuple[int, int, int]:
    """``(in, out, cached)`` under the convention shared with the read model."""
    tokens_in = sum(_int(usage.get(k)) for k in _IN_KEYS)
    tokens_out = sum(_int(usage.get(k)) for k in _OUT_KEYS)
    if tokens_out <= 0:
        tokens_out = sum(_int(usage.get(k)) for k in _OUT_DETAIL_KEYS)
    tokens_cached = sum(_int(usage.get(k)) for k in _CACHED_KEYS)
    return tokens_in, tokens_out, tokens_cached


def _flatten_details(usage: Mapping[str, Any]) -> dict[str, Any]:
    """Lift ``output_tokens_details`` to the top so one rule covers everything.

    A top-level key wins: the nested block is a breakdown of it, never a
    correction to it.
    """
    flat: dict[str, Any] = dict(usage)
    details = usage.get("output_tokens_details")
    if isinstance(details, Mapping):
        for key, value in details.items():
            flat.setdefault(key, value)
    return flat


def _payload(record: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = record.get(key)
    return value if isinstance(value, dict) else {}


def _key_of(path: Path) -> str:
    """Stable database key for a file — case-folded where the OS folds it."""
    return os.path.normcase(str(path))


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Candidate:
    path: Path
    key: str
    agent: str
    size: int
    mtime_ns: int


def _account_roots(platform: str) -> list[Path]:
    """Every extra login of a CLI the app manages (:mod:`jarvis.agent_accounts`).

    A second subscription runs with its own config directory under the app's
    data dir, and the CLI writes that account's transcripts THERE, not under
    ``~/.claude``. Three such accounts held 724 session files nobody counted
    (2026-08-25). The registry is optional here: the index must work on a
    machine — or in a test — where it cannot be loaded.
    """
    try:
        from jarvis import agent_accounts
    except Exception as exc:  # noqa: BLE001 — the index never needs the registry to run
        log.debug("cli usage index: account registry unavailable (%s)", exc)
        return []
    roots: list[Path] = []
    try:
        if platform in agent_accounts.platforms():
            roots.extend(
                Path(account.config_dir)
                for account in agent_accounts.list_accounts(platform)  # type: ignore[arg-type]
                if not account.builtin
            )
    except Exception as exc:  # noqa: BLE001 — a broken store must not stop the scan
        log.warning("cli usage index: could not list %s accounts (%s)", platform, exc)
    # A deleted account keeps its directory unless the user asked to remove
    # the files, and its spend was real. Every directory under the platform's
    # accounts folder counts, registered or not.
    try:
        from jarvis.core.paths import user_data_dir

        folder = user_data_dir() / "agent-accounts" / platform
        if folder.is_dir():
            roots.extend(p for p in folder.iterdir() if p.is_dir())
    except Exception as exc:  # noqa: BLE001 — same rule: the scan goes on
        log.debug("cli usage index: accounts folder for %s unreadable (%s)", platform, exc)
    return roots


def _dedup_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = _key_of(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _claude_roots(home: Path | None) -> list[Path]:
    """Claude Code config dirs: the CLI's own override, the default, and every
    managed account's directory."""
    if home is not None:
        return [home / ".claude"]
    roots: list[Path] = []
    override = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if override:
        roots.append(Path(override).expanduser())
    roots.append(Path.home() / ".claude")
    roots.extend(_account_roots("claude"))
    return _dedup_paths(roots)


def _codex_roots(home: Path | None) -> list[Path]:
    """``CODEX_HOME`` when the CLI is pointed elsewhere, the default, and every
    managed account's directory."""
    if home is not None:
        return [home / ".codex"]
    roots: list[Path] = []
    override = os.environ.get("CODEX_HOME", "").strip()
    if override:
        roots.append(Path(override).expanduser())
    roots.append(Path.home() / ".codex")
    roots.extend(_account_roots("codex"))
    return _dedup_paths(roots)


def _kimi_roots(home: Path | None) -> list[Path]:
    """Both Kimi generations. A machine can carry both histories at once."""
    base = home if home is not None else Path.home()
    return [base / ".kimi", base / ".kimi-code"]


def _antigravity_roots(home: Path | None) -> list[Path]:
    """``GEMINI_HOME`` when set (that directory IS ``.gemini``), else ``~/.gemini``."""
    if home is not None:
        return [home / ".gemini"]
    roots: list[Path] = []
    override = os.environ.get("GEMINI_HOME", "").strip()
    if override:
        roots.append(Path(override).expanduser())
    roots.append(Path.home() / ".gemini")
    return _dedup_paths(roots)


def _opencode_roots(home: Path | None) -> list[Path]:
    """``$XDG_DATA_HOME/opencode``, else ``~/.local/share/opencode`` — the
    same path on every OS, which is how OpenCode itself resolves it."""
    if home is not None:
        return [home / ".local" / "share" / "opencode"]
    roots: list[Path] = []
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg:
        roots.append(Path(xdg).expanduser() / "opencode")
    roots.append(Path.home() / ".local" / "share" / "opencode")
    return _dedup_paths(roots)


def _grok_roots(home: Path | None) -> list[Path]:
    """``GROK_HOME`` when set, the default, and every managed account."""
    if home is not None:
        return [home / ".grok"]
    roots: list[Path] = []
    override = os.environ.get("GROK_HOME", "").strip()
    if override:
        roots.append(Path(override).expanduser())
    roots.append(Path.home() / ".grok")
    roots.extend(_account_roots("grok-build"))
    return _dedup_paths(roots)


#: ``agent -> (root resolver, glob patterns relative to the root)``. Patterns
#: are explicit rather than ``rglob`` so discovery walks only the directories
#: that can hold transcripts.
_LAYOUTS: tuple[tuple[str, str], ...] = (
    (AGENT_CLAUDE, "projects/*/*.jsonl"),
    (AGENT_CODEX, "sessions/*/*/*/rollout-*.jsonl"),
    # Older Codex builds filed rollouts flat.
    (AGENT_CODEX, "sessions/rollout-*.jsonl"),
    # Kimi, legacy layout (``sessions/<md5>/<session>/wire.jsonl``).
    (AGENT_KIMI, "sessions/*/*/wire.jsonl"),
    # Kimi, current layout (``sessions/<wd_...>/<session>/agents/<name>/wire.jsonl``).
    (AGENT_KIMI, "sessions/*/*/agents/*/wire.jsonl"),
    # Antigravity: one SQLite conversation per UUID.
    (AGENT_AGY, "antigravity-cli/conversations/*.db"),
    # Grok Build: one folder per session under a url-encoded cwd.
    (AGENT_GROK, "sessions/*/*/updates.jsonl"),
    # OpenCode: the one SQLite store.
    (AGENT_OPENCODE, "opencode.db"),
)


def _roots_for(agent: str, home: Path | None) -> list[Path]:
    if agent == AGENT_CLAUDE:
        return _claude_roots(home)
    if agent == AGENT_CODEX:
        return _codex_roots(home)
    if agent == AGENT_GROK:
        return _grok_roots(home)
    if agent == AGENT_OPENCODE:
        return _opencode_roots(home)
    if agent == AGENT_AGY:
        return _antigravity_roots(home)
    return _kimi_roots(home)


def _sqlite_stat(path: Path) -> tuple[int, int]:
    """``(size, mtime_ns)`` for a SQLite file, WAL included.

    Antigravity (and any SQLite store) writes new turns into the WAL while
    the ``.db`` file stays the same size. Counting only the main file would
    skip a live conversation until the next checkpoint.
    """
    try:
        stat = path.stat()
    except OSError:
        return 0, 0
    size = stat.st_size
    mtime_ns = stat.st_mtime_ns
    try:
        wal = path.with_name(path.name + "-wal").stat()
    except OSError:
        return size, mtime_ns
    return size + wal.st_size, max(mtime_ns, wal.st_mtime_ns)


def _discover(home: Path | None) -> list[_Candidate]:
    """Every transcript on this machine, with its size and mtime.

    A root that does not exist is simply not there — that is the state of a
    fresh machine, and of every machine that never installed a given CLI.
    """
    found: dict[str, _Candidate] = {}
    for agent, pattern in _LAYOUTS:
        for root in _roots_for(agent, home):
            try:
                if not root.is_dir():
                    continue
                paths = list(root.glob(pattern))
            except OSError as exc:
                log.debug("cli usage index: %s not searchable (%s)", root, exc)
                continue
            for path in paths:
                key = _key_of(path)
                if key in found:
                    continue
                try:
                    if path.suffix == ".db":
                        size, mtime_ns = _sqlite_stat(path)
                    else:
                        stat = path.stat()
                        size, mtime_ns = stat.st_size, stat.st_mtime_ns
                except OSError as exc:
                    log.debug("cli usage index: %s not stat-able (%s)", path, exc)
                    continue
                found[key] = _Candidate(
                    path=path,
                    key=key,
                    agent=agent,
                    size=size,
                    mtime_ns=mtime_ns,
                )
    return list(found.values())


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Cursor:
    """What a file has told us so far, carried forward and then persisted.

    Codex puts the model on a record of its own, far from the usage records it
    applies to. Resuming a half-read file therefore has to start from the model
    the last run left off with, which is why this is stored rather than derived.
    """

    session_id: str = ""
    model: str = ""
    cwd: str = ""
    label: str = ""


class _LineReader:
    """Line iteration that keeps an exact byte offset.

    Binary mode, deliberately: text mode rewrites ``\\r\\n`` on Windows, and an
    offset counted from the rewritten text would point into the middle of a
    line on the next run. A trailing line without its newline is a file being
    appended to right now — it is left for the next run, never half-parsed.
    """

    __slots__ = ("_fh", "offset", "bytes_read", "reason")

    def __init__(self, fh: IO[bytes] | None, start: int) -> None:
        self._fh = fh
        self.offset = start
        self.bytes_read = 0
        self.reason = "eof"

    def attach(self, fh: IO[bytes]) -> None:
        """Bind the open handle. Constructed unbound so a failed open still
        leaves the caller a reader to report the untouched offset from."""
        self._fh = fh

    def lines(self, *, byte_cap: int, deadline: float) -> Iterator[tuple[int, bytes]]:
        if self._fh is None:
            return
        since_check = 0
        for raw in self._fh:
            if not raw.endswith(b"\n"):
                self.reason = "partial"
                return
            start = self.offset
            self.offset += len(raw)
            self.bytes_read += len(raw)
            yield start, raw
            if self.bytes_read >= byte_cap:
                self.reason = "cap"
                return
            since_check += 1
            if since_check >= _DEADLINE_CHECK_LINES:
                since_check = 0
                if time.monotonic() >= deadline:
                    self.reason = "deadline"
                    return


def _decode(raw: bytes) -> dict[str, Any] | None:
    """One JSONL line to a record, or ``None`` when it is not usable.

    UTF-8 with replacement: a byte sequence a CLI wrote in another encoding
    costs that one string, never the file.
    """
    try:
        record = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError as exc:
        log.debug("cli usage index: unparsable line skipped (%s)", exc)
        return None
    return record if isinstance(record, dict) else None


# ---------------------------------------------------------------------------
# Per-agent readers
# ---------------------------------------------------------------------------

# One row as the turn table takes it.
_Row = tuple[str, str, str, str, int, str, int, int, int, str, str]
#: A row plus the price the CLI recorded. Readers without one emit ``_Row``
#: and the writer pads it, so four readers stay untouched by the column.
_PricedRow = tuple[str, str, str, str, int, str, int, int, int, str, str, float]


def _claude_row(
    record: Mapping[str, Any], cand: _Candidate, cursor: _Cursor
) -> _Row | None:
    if record.get("type") != "assistant":
        return None
    message = _payload(record, "message")
    usage = message.get("usage")
    if not isinstance(usage, Mapping):
        return None
    tokens_in, tokens_out, tokens_cached = _usage_totals(_flatten_details(usage))
    if tokens_in + tokens_out + tokens_cached <= 0:
        return None
    # See the module docstring: ``uuid`` is per content block, ``message.id``
    # is per API response, and the API response is what was billed.
    dedup = str(message.get("id") or record.get("requestId") or record.get("uuid") or "")
    if not dedup:
        return None
    session_id = str(record.get("sessionId") or record.get("session_id") or cursor.session_id)
    if session_id:
        cursor.session_id = session_id
    cwd = str(record.get("cwd") or cursor.cwd)
    if cwd:
        cursor.cwd = cwd
        cursor.label = _clip(Path(cwd).name)
    return (
        cand.agent,
        dedup,
        cand.key,
        session_id or cand.path.stem,
        _iso_ms(record.get("timestamp")),
        str(message.get("model") or ""),
        tokens_in,
        tokens_out,
        tokens_cached,
        cwd,
        cursor.label,
    )


def _codex_row(
    record: Mapping[str, Any], offset: int, cand: _Candidate, cursor: _Cursor
) -> _Row | None:
    kind = record.get("type")
    if kind == "session_meta":
        meta = _payload(record, "payload")
        cursor.session_id = str(meta.get("session_id") or meta.get("id") or cursor.session_id)
        cwd = str(meta.get("cwd") or "")
        if cwd:
            cursor.cwd = cwd
            cursor.label = _clip(Path(cwd).name)
        return None
    if kind == "turn_context":
        context = _payload(record, "payload")
        cursor.model = str(context.get("model") or cursor.model)
        cwd = str(context.get("cwd") or "")
        if cwd and not cursor.cwd:
            cursor.cwd = cwd
            cursor.label = _clip(Path(cwd).name)
        return None
    if kind != "event_msg":
        return None
    payload = _payload(record, "payload")
    if payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, Mapping):
        # A session emits ``info: null`` before its first model call. Nothing
        # was spent, so there is nothing to record and nothing to report.
        return None
    # ``last_token_usage`` only: ``total_token_usage`` is cumulative and
    # summing it would multiply the real number by the turn count.
    usage = info.get("last_token_usage")
    if not isinstance(usage, Mapping):
        return None
    tokens_in, tokens_out, tokens_cached = _usage_totals(usage)
    if tokens_in + tokens_out + tokens_cached <= 0:
        return None
    # OpenAI counts cache hits INSIDE ``input_tokens``; the convention here
    # (and Anthropic's) keeps them apart. See the module docstring.
    tokens_in = max(0, tokens_in - tokens_cached)
    session_id = cursor.session_id or _codex_session_id(cand.path)
    return (
        cand.agent,
        _codex_dedup_key(session_id, info, cand, offset),
        cand.key,
        session_id,
        _iso_ms(record.get("timestamp")),
        cursor.model,
        tokens_in,
        tokens_out,
        tokens_cached,
        cursor.cwd,
        cursor.label,
    )


def _codex_dedup_key(
    session_id: str, info: Mapping[str, Any], cand: _Candidate, offset: int
) -> str:
    """One key per real model call, the same in every file that replays it.

    The cumulative ``total_token_usage`` after a call is unique within a
    lineage (it only ever grows), so ``session_id`` plus that total names the
    call wherever it is written down. Records without a running total fall
    back to the line's place in the file — a rewritten file has its rows
    dropped first, so an offset never means two different turns.
    """
    total = info.get("total_token_usage")
    if isinstance(total, Mapping) and session_id:
        counts = [
            _int(total.get(k))
            for k in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")
        ]
        if any(counts):
            return f"{session_id}:" + ":".join(str(c) for c in counts)
    return f"{cand.key}:{offset}"


def _codex_session_id(path: Path) -> str:
    match = _ROLLOUT_ID.search(path.stem)
    return match.group(1) if match else path.stem


def _agy_row(
    record: Mapping[str, Any], offset: int, cand: _Candidate, cursor: _Cursor
) -> _Row | None:
    message = _payload(record, "message")
    if message.get("type") != "StatusUpdate":
        return None
    payload = _payload(message, "payload")
    usage = payload.get("token_usage")
    if not isinstance(usage, Mapping):
        return None
    renamed: dict[str, Any] = {
        _AGY_KEY_MAP.get(str(key), str(key)): value for key, value in usage.items()
    }
    tokens_in, tokens_out, tokens_cached = _usage_totals(renamed)
    if tokens_in + tokens_out + tokens_cached <= 0:
        return None
    return (
        cand.agent,
        f"{cand.key}:{offset}",
        cand.key,
        cursor.session_id,
        _epoch_ms(record.get("timestamp")),
        # agy writes no model id into its transcript, at any point.
        "",
        tokens_in,
        tokens_out,
        tokens_cached,
        cursor.cwd,
        cursor.label,
    )


def _grok_row(
    record: Mapping[str, Any], offset: int, cand: _Candidate, cursor: _Cursor
) -> _Row | None:
    params = _payload(record, "params")
    update = _payload(params, "update")
    if update.get("sessionUpdate") != "turn_completed":
        return None
    usage = update.get("usage")
    if not isinstance(usage, Mapping):
        return None
    cached = _int(usage.get("cachedReadTokens"))
    tokens_in = max(0, _int(usage.get("inputTokens")) - cached) + _int(
        usage.get("cacheCreationTokens")
    )
    tokens_out = _int(usage.get("outputTokens"))
    if tokens_in + tokens_out + cached <= 0:
        return None
    model = cursor.model
    per_model = usage.get("modelUsage")
    if isinstance(per_model, Mapping) and per_model:
        model = str(next(iter(per_model.keys())) or model)
    session_id = str(params.get("sessionId") or cursor.session_id)
    prompt_id = str(update.get("prompt_id") or "")
    dedup = f"{session_id}:{prompt_id}" if session_id and prompt_id else f"{cand.key}:{offset}"
    return (
        cand.agent,
        dedup,
        cand.key,
        session_id,
        _epoch_ms(record.get("timestamp")),
        model,
        tokens_in,
        tokens_out,
        cached,
        cursor.cwd,
        cursor.label,
    )


def _grok_context(path: Path) -> tuple[str, str, str, str]:
    """``(session_id, cwd, label, model)`` from the folder's ``summary.json``."""
    folder = path.parent
    session_id = folder.name
    cwd = ""
    model = ""
    summary = folder / "summary.json"
    try:
        if summary.is_file():
            parsed = json.loads(summary.read_text(encoding="utf-8", errors="replace"))
            if isinstance(parsed, dict):
                raw_info = parsed.get("info")
                info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
                session_id = str(info.get("id") or session_id)
                cwd = str(info.get("cwd") or "")
                model = str(parsed.get("current_model_id") or "")
    except (OSError, ValueError) as exc:
        log.debug("cli usage index: %s unreadable (%s)", summary, exc)
    label = _clip(Path(cwd).name if cwd else session_id)
    return session_id, cwd, label, model


def _agy_session_dir(path: Path) -> Path:
    """The session folder of a ``wire.jsonl``, in either layout."""
    if path.parent.parent.name == "agents":
        return path.parent.parent.parent
    return path.parent


def _agy_context(path: Path) -> tuple[str, str, str]:
    """``(session_id, cwd, label)`` for a wire log.

    The current layout records the working directory in the session's
    ``state.json``. The legacy layout records it nowhere — its bucket name is
    an MD5 of the directory — so an empty ``cwd`` there is the honest answer
    rather than a reconstruction.
    """
    folder = _agy_session_dir(path)
    session_id = folder.name
    state = folder / "state.json"
    cwd = ""
    title = ""
    try:
        if state.is_file():
            parsed = json.loads(state.read_text(encoding="utf-8", errors="replace"))
            if isinstance(parsed, dict):
                cwd = str(parsed.get("workDir") or "")
                title = str(parsed.get("title") or "")
    except (OSError, ValueError) as exc:
        log.debug("cli usage index: %s unreadable (%s)", state, exc)
    label = _clip(Path(cwd).name if cwd else title or session_id)
    return session_id, cwd, label


# ---------------------------------------------------------------------------
# Scanning one file
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _FileScan:
    rows: list[_Row]
    offset: int
    cursor: _Cursor
    bytes_read: int
    reason: str
    failed: bool = False


_AGY_MODEL_RE = re.compile(
    r"^(gemini-[\w.-]+|claude-[\w.-]+|gpt-oss-[\w.-]+)$", re.IGNORECASE
)
_AGY_EFFORT_SUFFIXES = ("-low", "-medium", "-high")


def _agy_model_id(raw: str) -> str:
    """Strip the effort suffix agy appends to Gemini ids (``-medium``)."""
    model = raw.strip()
    for suffix in _AGY_EFFORT_SUFFIXES:
        if model.lower().endswith(suffix):
            return model[: -len(suffix)]
    return model


def _pb_varint(buf: bytes, i: int) -> tuple[int, int] | None:
    n = 0
    shift = 0
    while i < len(buf):
        byte = buf[i]
        i += 1
        n |= (byte & 0x7F) << shift
        if byte < 0x80:
            return n, i
        shift += 7
        if shift > 70:
            return None
    return None


def _pb_fields(buf: bytes) -> list[tuple[int, int, bytes | int]]:
    """``(field, wire_type, value)`` for one protobuf message. Never raises."""
    out: list[tuple[int, int, bytes | int]] = []
    i = 0
    n = len(buf)
    while i < n:
        parsed = _pb_varint(buf, i)
        if parsed is None:
            break
        key, i = parsed
        field, wtype = key >> 3, key & 7
        if wtype == 0:
            parsed = _pb_varint(buf, i)
            if parsed is None:
                break
            val, i = parsed
            out.append((field, wtype, val))
        elif wtype == 1:
            if i + 8 > n:
                break
            out.append((field, wtype, int.from_bytes(buf[i : i + 8], "little")))
            i += 8
        elif wtype == 2:
            parsed = _pb_varint(buf, i)
            if parsed is None:
                break
            length, i = parsed
            if i + length > n:
                break
            out.append((field, wtype, buf[i : i + length]))
            i += length
        elif wtype == 5:
            if i + 4 > n:
                break
            out.append((field, wtype, int.from_bytes(buf[i : i + 4], "little")))
            i += 4
        else:
            break
    return out


def _agy_decode_str(value: bytes | int) -> str:
    if not isinstance(value, (bytes, bytearray)):
        return ""
    try:
        text = bytes(value).decode("utf-8")
    except UnicodeDecodeError:
        return ""
    return text if text.isprintable() or "\n" in text else ""


def _agy_usage_from_fields(
    fields: list[tuple[int, int, bytes | int]],
) -> tuple[int, int, int] | None:
    """A nested message is usage when it names a bot and carries token counts.

    Shape measured on agy 1.1.20 ``gen_metadata`` blobs: field 2 = uncached
    input, field 3 = output, field 5 = cache read, field 7 = ``bot-<uuid>``.
    """
    by_field: dict[int, list[bytes | int]] = {}
    for field, _wtype, value in fields:
        by_field.setdefault(field, []).append(value)
    bot = next((_agy_decode_str(v) for v in by_field.get(7, [])), "")
    if not bot.startswith("bot-"):
        return None
    tokens_in = next((int(v) for v in by_field.get(2, []) if isinstance(v, int)), 0)
    tokens_out = next((int(v) for v in by_field.get(3, []) if isinstance(v, int)), 0)
    tokens_cached = next((int(v) for v in by_field.get(5, []) if isinstance(v, int)), 0)
    if tokens_in + tokens_out + tokens_cached <= 0:
        return None
    return tokens_in, tokens_out, tokens_cached


def _agy_collect_usage(
    buf: bytes, found: list[tuple[int, int, int]], depth: int = 0
) -> None:
    if depth > 8 or not buf:
        return
    fields = _pb_fields(buf)
    usage = _agy_usage_from_fields(fields)
    if usage is not None:
        found.append(usage)
        return
    for _field, wtype, value in fields:
        if wtype == 2 and isinstance(value, (bytes, bytearray)):
            _agy_collect_usage(bytes(value), found, depth + 1)


def _agy_model_from_blob(buf: bytes, depth: int = 0) -> str:
    if depth > 6 or not buf:
        return ""
    for _field, wtype, value in _pb_fields(buf):
        if wtype != 2:
            continue
        text = _agy_decode_str(value if isinstance(value, (bytes, bytearray)) else b"")
        if text and _AGY_MODEL_RE.match(text):
            return _agy_model_id(text)
    for _field, wtype, value in _pb_fields(buf):
        if wtype == 2 and isinstance(value, (bytes, bytearray)):
            nested = _agy_model_from_blob(bytes(value), depth + 1)
            if nested:
                return nested
    return ""


def _agy_cwd_from_blob(buf: bytes) -> str:
    """Best-effort working directory from a ``file:///`` URI in the blob.

    The URI sits inside a protobuf length-delimited string, so the bytes
    after it are the next field, not path characters. Stop at the first
    character a file URI cannot contain.
    """
    try:
        text = buf.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""
    marker = "file:///"
    start = text.lower().find(marker)
    if start < 0:
        return ""
    end = start + len(marker)
    while end < len(text) and (text[end].isalnum() or text[end] in ":/%._-"):
        end += 1
    uri = text[start:end]
    path = uri[len("file://") :]  # keep the slash that starts a POSIX path
    if path.startswith("/") and len(path) >= 3 and path[2] == ":":
        path = path[1:]
    from urllib.parse import unquote

    return unquote(path)


def _scan_antigravity(cand: _Candidate, start: int) -> _FileScan:
    """Read one Antigravity conversation database.

    ``start`` is unused: a conversation DB is small and the WAL can rewrite
    earlier rows, so a change always re-reads. Dedup is ``session_id:idx``.
    """
    del start
    rows: list[_Row] = []
    session_id = cand.path.stem
    cwd = ""
    model = ""
    failed = False
    try:
        uri = f"file:{cand.path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=_DB_TIMEOUT_S)
    except sqlite3.Error as exc:
        log.warning("cli usage index: %s not readable (%s)", cand.path, exc)
        return _FileScan(
            rows=[], offset=cand.size, cursor=_Cursor(), bytes_read=0, reason="error", failed=True
        )
    try:
        conn.row_factory = sqlite3.Row
        try:
            blob_row = conn.execute(
                "SELECT data FROM trajectory_metadata_blob LIMIT 1"
            ).fetchone()
        except sqlite3.Error:
            blob_row = None
        if blob_row is not None and blob_row["data"]:
            cwd = _agy_cwd_from_blob(blob_row["data"])
        try:
            meta_rows = list(conn.execute("SELECT idx, data FROM gen_metadata ORDER BY idx"))
        except sqlite3.Error as exc:
            log.debug("cli usage index: %s has no gen_metadata (%s)", cand.path, exc)
            meta_rows = []
        ts_ms = cand.mtime_ns // 1_000_000
        label = _clip(Path(cwd).name if cwd else session_id)
        for meta in meta_rows:
            blob = meta["data"]
            if not blob:
                continue
            if not model:
                model = _agy_model_from_blob(blob)
            found: list[tuple[int, int, int]] = []
            _agy_collect_usage(blob, found)
            idx = _int(meta["idx"])
            for call_i, (tokens_in, tokens_out, tokens_cached) in enumerate(found):
                rows.append(
                    (
                        cand.agent,
                        f"{session_id}:{idx}:{call_i}",
                        cand.key,
                        session_id,
                        ts_ms,
                        model,
                        tokens_in,
                        tokens_out,
                        tokens_cached,
                        cwd,
                        label,
                    )
                )
    except sqlite3.Error as exc:
        log.warning("cli usage index: %s query failed (%s)", cand.path, exc)
        failed = True
    finally:
        conn.close()
    return _FileScan(
        rows=rows,
        offset=cand.size,
        cursor=_Cursor(
            session_id=session_id,
            model=model,
            cwd=cwd,
            label=_clip(Path(cwd).name if cwd else session_id),
        ),
        bytes_read=cand.size,
        reason="eof",
        failed=failed,
    )


def _scan_opencode(cand: _Candidate, start: int) -> _FileScan:
    """Read OpenCode's store. ``start`` is the newest ``time_created`` already
    indexed (the "offset" column holds a timestamp for this agent), so a run
    reads only what arrived since. A store that shrank (vacuum) re-reads from
    zero and the message-id key keeps every turn counted once."""
    rows: list[_PricedRow] = []
    newest = start
    failed = False
    try:
        uri = f"file:{cand.path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=_DB_TIMEOUT_S)
    except sqlite3.Error as exc:
        log.warning("cli usage index: %s not readable (%s)", cand.path, exc)
        return _FileScan(
            rows=[], offset=start, cursor=_Cursor(), bytes_read=0, reason="error", failed=True
        )
    try:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(
            "SELECT m.id, m.session_id, m.time_created, m.data, s.directory "
            "FROM message m LEFT JOIN session s ON s.id = m.session_id "
            "WHERE CAST(m.time_created AS INTEGER) > ? ORDER BY m.time_created",
            (start,),
        ):
            ts_ms = _int(row["time_created"])
            newest = max(newest, ts_ms)
            try:
                data = json.loads(row["data"] or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict) or data.get("role") != "assistant":
                continue
            raw_tokens = data.get("tokens")
            tokens: dict[str, Any] = raw_tokens if isinstance(raw_tokens, dict) else {}
            raw_cache = tokens.get("cache")
            cache: dict[str, Any] = raw_cache if isinstance(raw_cache, dict) else {}
            tokens_in = _int(tokens.get("input")) + _int(cache.get("write"))
            tokens_out = _int(tokens.get("output"))
            if tokens_out <= 0:
                tokens_out = _int(tokens.get("reasoning"))
            tokens_cached = _int(cache.get("read"))
            if tokens_in + tokens_out + tokens_cached <= 0:
                continue
            cwd = str(row["directory"] or "")
            raw_path = data.get("path")
            path_info: dict[str, Any] = raw_path if isinstance(raw_path, dict) else {}
            cwd = cwd or str(path_info.get("cwd") or "")
            rows.append(
                (
                    cand.agent,
                    str(row["id"]),
                    cand.key,
                    str(row["session_id"] or ""),
                    ts_ms,
                    str(data.get("modelID") or ""),
                    tokens_in,
                    tokens_out,
                    tokens_cached,
                    cwd,
                    _clip(Path(cwd).name) if cwd else "",
                    float(data.get("cost") or 0.0),
                )
            )
    except sqlite3.Error as exc:
        log.warning("cli usage index: %s query failed (%s)", cand.path, exc)
        failed = True
    finally:
        conn.close()
    return _FileScan(
        rows=list(rows),  # type: ignore[arg-type]
        offset=max(newest, cand.size),
        cursor=_Cursor(),
        bytes_read=cand.size,
        reason="eof",
        failed=failed,
    )


def _scan(cand: _Candidate, start: int, cursor: _Cursor, deadline: float) -> _FileScan:
    """Read one transcript from ``start`` and return the turns it added."""
    if cand.agent == AGENT_OPENCODE:
        return _scan_opencode(cand, start)
    if cand.agent == AGENT_AGY:
        return _scan_antigravity(cand, start)
    rows: list[_Row] = []
    reader = _LineReader(None, start)
    if cand.agent == AGENT_KIMI and not cursor.session_id:
        cursor.session_id, cursor.cwd, cursor.label = _agy_context(cand.path)
    if cand.agent == AGENT_GROK and not cursor.session_id:
        cursor.session_id, cursor.cwd, cursor.label, model = _grok_context(cand.path)
        cursor.model = cursor.model or model
    failed = False
    try:
        with cand.path.open("rb") as fh:
            if start:
                fh.seek(start)
            reader.attach(fh)
            for offset, raw in reader.lines(byte_cap=_FILE_BYTE_BUDGET, deadline=deadline):
                if not _wanted(cand.agent, raw):
                    continue
                record = _decode(raw)
                if record is None:
                    continue
                row = _row_for(cand.agent, record, offset, cand, cursor)
                if row is not None:
                    rows.append(row)
    except OSError as exc:
        # Partial progress still counts: the lines already parsed are real and
        # the offset stops exactly where reading stopped, so the next run picks
        # up from there rather than from the start.
        log.warning("cli usage index: %s not readable (%s)", cand.path, exc)
        reader.reason = "error"
        failed = True
    return _FileScan(
        rows=rows,
        offset=reader.offset,
        cursor=cursor,
        bytes_read=reader.bytes_read,
        reason=reader.reason,
        failed=failed,
    )


def _wanted(agent: str, raw: bytes) -> bool:
    """The pre-filter that keeps gigabytes of JSON out of ``json.loads``."""
    if agent == AGENT_CLAUDE:
        return _CLAUDE_MARK in raw
    if agent == AGENT_CODEX:
        return any(mark in raw for mark in _CODEX_MARKS)
    if agent == AGENT_GROK:
        return _GROK_MARK in raw
    if agent == AGENT_KIMI:
        return _AGY_MARK in raw
    return False


def _row_for(
    agent: str, record: Mapping[str, Any], offset: int, cand: _Candidate, cursor: _Cursor
) -> _Row | None:
    if agent == AGENT_CLAUDE:
        return _claude_row(record, cand, cursor)
    if agent == AGENT_CODEX:
        return _codex_row(record, offset, cand, cursor)
    if agent == AGENT_GROK:
        return _grok_row(record, offset, cand, cursor)
    if agent == AGENT_KIMI:
        return _agy_row(record, offset, cand, cursor)
    return None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def index_db_path(data_dir: Path | None = None) -> Path:
    """Where the index lives.

    The data dir — not the process CWD — decides, exactly as
    ``jarvis.costs.sources.default_sources`` does, so a second instance
    (``JARVIS_INSTANCE=dev``) indexes into its own file.
    """
    root = data_dir
    if root is None:
        from jarvis.core import config as cfg

        root = Path(cfg.DATA_DIR)
    return Path(root) / DB_NAME


def _open_rw(path: Path) -> sqlite3.Connection | None:
    """Open (creating if needed) the index for writing, or ``None``."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=_DB_TIMEOUT_S)
        conn.row_factory = sqlite3.Row
        # WAL so a report reading the index is never blocked by a refresh, and
        # NORMAL so a run that touches thousands of files is not thousands of
        # fsyncs. A lost tail after a crash costs a re-read, never a wrong sum.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        version = _int(conn.execute("PRAGMA user_version").fetchone()[0])
        if _MIGRATE_IN_PLACE_FROM <= version < _SCHEMA_VERSION:
            # Rows counted under this version are right; only the shape grew.
            columns = {r[1] for r in conn.execute("PRAGMA table_info(cli_turns)")}
            if "cost_usd" not in columns:
                conn.execute("ALTER TABLE cli_turns ADD COLUMN cost_usd REAL NOT NULL DEFAULT 0")
            conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            version = _SCHEMA_VERSION
        if version < _SCHEMA_VERSION:
            # Rows counted under an older rule are wrong, not stale. Rebuild.
            if _has_rows(conn):
                log.info(
                    "cli usage index: schema %d < %d, rebuilding from the transcripts",
                    version,
                    _SCHEMA_VERSION,
                )
            conn.executescript(
                "DROP TABLE IF EXISTS cli_turns; DROP TABLE IF EXISTS indexed_files;"
            )
            conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        conn.executescript(_SCHEMA)
        conn.commit()
        return conn
    except (OSError, sqlite3.Error) as exc:
        log.warning("cli usage index: %s not writable (%s)", path, exc)
        return None


def _has_rows(conn: sqlite3.Connection) -> bool:
    """Whether an index holds any turn at all (decides if a rebuild is news)."""
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cli_turns'"
        ).fetchone()
        if table is None:
            return False
        return conn.execute("SELECT 1 FROM cli_turns LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


def _open_ro(path: Path) -> sqlite3.Connection | None:
    """Open the index read-only, or ``None`` when there is nothing to read."""
    if not path.exists():
        return None
    try:
        # Percent-encoded: an unencoded space or ``#`` in the path makes SQLite
        # open a DIFFERENT, empty database instead of failing.
        uri = f"file:{quote(path.as_posix(), safe='/:')}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=_DB_TIMEOUT_S)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:
        log.warning("cli usage index: %s not readable (%s)", path, exc)
        return None


def _backfill_models(conn: sqlite3.Connection, keys: list[str]) -> None:
    """Give model-less rows of the files just scanned the model their session
    recorded elsewhere.

    A Codex fork replays its parent before its own ``turn_context``, so those
    rows are inserted with no model, and the parent file — scanned later or
    earlier — is where the model lives. Scoped to the scanned files so a
    refresh never pays for the whole table (the unscoped sweep took 12 s on
    132k rows, 2026-08-25).
    """
    sql = (
        "UPDATE cli_turns SET model = ("
        " SELECT t2.model FROM cli_turns t2"
        "  WHERE t2.agent = cli_turns.agent AND t2.session_id = cli_turns.session_id"
        "    AND t2.model <> '' ORDER BY t2.ts_ms LIMIT 1)"
        " WHERE model = '' AND session_id <> '' AND path = ? AND EXISTS ("
        " SELECT 1 FROM cli_turns t3 WHERE t3.agent = cli_turns.agent"
        "   AND t3.session_id = cli_turns.session_id AND t3.model <> '')"
    )
    conn.executemany(sql, [(key,) for key in keys])


def _prune_vanished(
    conn: sqlite3.Connection,
    candidates: list[_Candidate],
    known: dict[str, sqlite3.Row],
    home: Path | None,
) -> None:
    """Drop the rows of transcripts that no longer exist.

    ``_drop_rows`` only fires when a file SHRANK; a deleted file kept its rows
    forever (446 rows / 88M tokens from six vanished transcripts,
    2026-08-25). Pruning is limited to roots that are readable right now, so
    an unmounted drive or a transient permission error never wipes history.
    """
    live: list[str] = []
    for agent, _pattern in _LAYOUTS:
        for root_dir in _roots_for(agent, home):
            try:
                if root_dir.is_dir():
                    live.append(_key_of(root_dir))
            except OSError:
                continue
    if not live:
        return
    fresh = {c.key for c in candidates}
    for key in list(known):
        if key in fresh or not any(key.startswith(r) for r in live):
            continue
        _drop_rows(conn, key)
        conn.execute("DELETE FROM indexed_files WHERE path = ?", (key,))
        known.pop(key, None)


def _resume_rows(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    try:
        return {
            str(row["path"]): row
            for row in conn.execute(
                "SELECT path, agent, session_id, size, mtime_ns, byte_offset, "
                "       model, cwd, label FROM indexed_files"
            )
        }
    except sqlite3.Error as exc:
        log.warning("cli usage index: resume state unreadable, rebuilding (%s)", exc)
        return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def refresh(
    *,
    data_dir: Path | None = None,
    deadline_s: float = 5.0,
    since_ms: int = 0,
    home: Path | None = None,
) -> RefreshResult:
    """Read whatever is new in the coding-CLI transcripts, within a budget.

    ``since_ms`` skips files last modified before that instant: they cannot
    have gained a turn the caller is asking about, and skipping them leaves
    their resume state untouched, so a later call with a wider window still
    picks them up in full.

    ``home`` replaces the user's home directory when resolving the CLI roots.
    It exists so tests never touch the real ``~/.claude`` or ``~/.codex``; in
    production it stays ``None`` and the CLIs' own environment overrides apply.

    Never raises. When the index cannot be opened at all the result reports
    nothing done and ``complete=False``.
    """
    started = time.monotonic()
    deadline = started + max(0.0, deadline_s)
    db_path = _resolve_db_path(data_dir)
    if db_path is None:
        return RefreshResult(0, 0, 0, 0, False, 0.0, 1)

    candidates = _discover(home)
    conn = _open_rw(db_path)
    if conn is None:
        return RefreshResult(len(candidates), 0, 0, 0, False, time.monotonic() - started, 1)

    scanned = 0
    bytes_read = 0
    turns_added = 0
    errors = 0
    complete = True
    scanned_keys: list[str] = []
    try:
        known = _resume_rows(conn)
        _prune_vanished(conn, candidates, known, home)
        pending = _pending(candidates, known, since_ms)
        # Newest first: the file a user just closed is the one whose numbers
        # they are looking at, and it is the one most likely to be small.
        pending.sort(key=lambda cand: cand.mtime_ns, reverse=True)
        for cand in pending:
            if time.monotonic() >= deadline:
                complete = False
                break
            row = known.get(cand.key)
            start, cursor = _resume_point(cand, row)
            if row is not None and start == 0:
                # The file shrank — rotated or rewritten. Its old rows describe
                # bytes that no longer exist, so they go before it is re-read.
                _drop_rows(conn, cand.key)
            scan = _scan(cand, start, cursor, deadline)
            scanned += 1
            scanned_keys.append(cand.key)
            bytes_read += scan.bytes_read
            if scan.failed:
                errors += 1
            if scan.reason in ("cap", "deadline"):
                complete = False
            turns_added += _commit_file(conn, cand, scan)
            if scan.reason == "deadline":
                break
        if scanned_keys:
            _backfill_models(conn, scanned_keys)
    except sqlite3.Error as exc:
        log.warning("cli usage index: refresh aborted (%s)", exc)
        errors += 1
        complete = False
    finally:
        try:
            conn.commit()
        except sqlite3.Error as exc:
            log.warning("cli usage index: final commit failed (%s)", exc)
        conn.close()
    return RefreshResult(
        files_seen=len(candidates),
        files_scanned=scanned,
        bytes_read=bytes_read,
        turns_added=turns_added,
        complete=complete,
        elapsed_s=time.monotonic() - started,
        errors=errors,
    )


def entries(
    *, data_dir: Path | None = None, since_ms: int, until_ms: int
) -> Iterator[CliTurn]:
    """Indexed turns in ``[since_ms, until_ms]``, oldest first.

    Reads only the index, never a transcript, so this is a millisecond
    operation whatever the logs weigh. An index that does not exist yet yields
    nothing — call :func:`refresh` to build it.
    """
    db_path = _resolve_db_path(data_dir)
    if db_path is None:
        return
    conn = _open_ro(db_path)
    if conn is None:
        return
    try:
        for row in conn.execute(
            "SELECT agent, session_id, ts_ms, model, tokens_in, tokens_out, "
            "       tokens_cached, cwd, label, cost_usd FROM cli_turns "
            "WHERE ts_ms BETWEEN ? AND ? ORDER BY ts_ms",
            (since_ms, until_ms),
        ):
            yield CliTurn(
                agent=str(row["agent"] or ""),
                session_id=str(row["session_id"] or ""),
                ts_ms=_int(row["ts_ms"]),
                model=str(row["model"] or ""),
                tokens_in=_int(row["tokens_in"]),
                tokens_out=_int(row["tokens_out"]),
                tokens_cached=_int(row["tokens_cached"]),
                cwd=str(row["cwd"] or ""),
                label=str(row["label"] or ""),
                cost_usd=float(row["cost_usd"] or 0.0),
            )
    except sqlite3.Error as exc:
        log.warning("cli usage index: read failed (%s)", exc)
    finally:
        conn.close()


def rollups(
    *, data_dir: Path | None = None, since_ms: int, until_ms: int, bucket_ms: int
) -> Iterator[CliRollup]:
    """Indexed turns summed per (agent, model, session, time bucket).

    ``bucket_ms`` should match the bucket the report itself uses — an hour for
    a short window, a day otherwise — so rolling up here never coarsens the
    chart above what it was going to draw anyway.

    ``cwd`` and ``label`` come from any row in the group: they describe the
    session, which is part of the group key, so every row in it agrees.
    """
    db_path = _resolve_db_path(data_dir)
    if db_path is None:
        return
    bucket = max(1, int(bucket_ms))
    conn = _open_ro(db_path)
    if conn is None:
        return
    try:
        for row in conn.execute(
            "SELECT agent, session_id, model, "
            "       MIN(ts_ms) AS ts_ms, "
            "       SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out, "
            "       SUM(tokens_cached) AS tokens_cached, COUNT(*) AS turns, "
            "       MIN(cwd) AS cwd, MIN(label) AS label, SUM(cost_usd) AS cost_usd "
            "FROM cli_turns WHERE ts_ms BETWEEN ? AND ? "
            "GROUP BY agent, session_id, model, ts_ms / ? "
            "ORDER BY ts_ms",
            (since_ms, until_ms, bucket),
        ):
            yield CliRollup(
                agent=str(row["agent"] or ""),
                session_id=str(row["session_id"] or ""),
                ts_ms=_int(row["ts_ms"]),
                model=str(row["model"] or ""),
                tokens_in=_int(row["tokens_in"]),
                tokens_out=_int(row["tokens_out"]),
                tokens_cached=_int(row["tokens_cached"]),
                turns=_int(row["turns"]),
                cwd=str(row["cwd"] or ""),
                label=str(row["label"] or ""),
                cost_usd=float(row["cost_usd"] or 0.0),
            )
    except sqlite3.Error as exc:
        log.warning("cli usage index: rollup failed (%s)", exc)
    finally:
        conn.close()


def index_state(*, data_dir: Path | None = None, home: Path | None = None) -> IndexState:
    """How much of the transcripts on disk the index has already absorbed.

    Walks the transcript directories (a few thousand ``stat`` calls) but opens
    no transcript, so it is cheap enough for a status badge and honest about
    files that appeared since the last :func:`refresh`.
    """
    db_path = _resolve_db_path(data_dir)
    if db_path is None:
        return IndexState(0, 0, 0, 0, 0, Path(DB_NAME))
    candidates = _discover(home)
    conn = _open_ro(db_path)
    known: dict[str, sqlite3.Row] = {}
    turns = 0
    if conn is not None:
        try:
            known = _resume_rows(conn)
            row = conn.execute("SELECT COUNT(*) AS n FROM cli_turns").fetchone()
            turns = _int(row["n"]) if row is not None else 0
        except sqlite3.Error as exc:
            log.warning("cli usage index: state unreadable (%s)", exc)
        finally:
            conn.close()

    indexed = 0
    bytes_pending = 0
    for cand in candidates:
        stored = known.get(cand.key)
        offset = _int(stored["byte_offset"]) if stored is not None else 0
        if stored is not None and _int(stored["size"]) > cand.size:
            # Shrank since the last run: everything will be read again.
            offset = 0
        if offset >= cand.size:
            indexed += 1
        else:
            bytes_pending += cand.size - offset
    return IndexState(
        files_known=len(candidates),
        files_indexed=indexed,
        files_pending=len(candidates) - indexed,
        bytes_pending=bytes_pending,
        turns=turns,
        db_path=db_path,
    )


# ---------------------------------------------------------------------------
# Refresh internals
# ---------------------------------------------------------------------------


def _resolve_db_path(data_dir: Path | None) -> Path | None:
    try:
        return index_db_path(data_dir)
    except Exception as exc:  # noqa: BLE001 - config import is the only risk here
        log.warning("cli usage index: data dir unresolvable (%s)", exc)
        return None


def _pending(
    candidates: Sequence[_Candidate], known: Mapping[str, sqlite3.Row], since_ms: int
) -> list[_Candidate]:
    """The files worth opening this run."""
    out: list[_Candidate] = []
    for cand in candidates:
        if since_ms > 0 and cand.mtime_ns // 1_000_000 < since_ms:
            continue
        row = known.get(cand.key)
        # A file only earns a skip when nothing about it moved AND the last run
        # reached its end. Same bytes but an offset short of them means the last
        # run stopped early — on its deadline, on the per-file cap, or on a
        # half-written trailing line — and there is still tail to read.
        if row is not None and _int(row["byte_offset"]) >= cand.size:
            if _int(row["size"]) == cand.size and _int(row["mtime_ns"]) == cand.mtime_ns:
                continue
        out.append(cand)
    return out


def _resume_point(cand: _Candidate, row: sqlite3.Row | None) -> tuple[int, _Cursor]:
    """Where to start reading, and what the file already told us."""
    if row is None:
        return 0, _Cursor()
    if _int(row["size"]) > cand.size:
        # Shrank: rotated or rewritten. Everything known about it is stale.
        return 0, _Cursor()
    offset = min(_int(row["byte_offset"]), cand.size)
    return offset, _Cursor(
        session_id=str(row["session_id"] or ""),
        model=str(row["model"] or ""),
        cwd=str(row["cwd"] or ""),
        label=str(row["label"] or ""),
    )


def _drop_rows(conn: sqlite3.Connection, key: str) -> None:
    try:
        conn.execute("DELETE FROM cli_turns WHERE path = ?", (key,))
    except sqlite3.Error as exc:
        log.warning("cli usage index: could not drop rows of %s (%s)", key, exc)


def _commit_file(conn: sqlite3.Connection, cand: _Candidate, scan: _FileScan) -> int:
    """Write a file's turns and its resume point as one transaction."""
    before = conn.total_changes
    try:
        if scan.rows:
            priced = [r if len(r) == 12 else (*r, 0.0) for r in scan.rows]
            conn.executemany(_INSERT_TURN, priced)
        added = conn.total_changes - before
        if scan.cursor.model:
            # A replayed prefix lands before the file's ``turn_context``; the
            # context that follows is authoritative for every row in the
            # file (130 Codex rows / 16.4M tokens sat unpriced, 2026-08-25).
            conn.execute(
                "UPDATE cli_turns SET model = ? WHERE path = ? AND agent = ? AND model = ''",
                (scan.cursor.model, cand.key, cand.agent),
            )
        conn.execute(
            _UPSERT_FILE,
            (
                cand.key,
                cand.agent,
                scan.cursor.session_id,
                cand.size,
                cand.mtime_ns,
                scan.offset,
                scan.cursor.model,
                scan.cursor.cwd,
                scan.cursor.label,
                int(time.time() * 1000),
            ),
        )
        conn.commit()
        return added
    except sqlite3.Error as exc:
        log.warning("cli usage index: %s could not be committed (%s)", cand.path, exc)
        try:
            conn.rollback()
        except sqlite3.Error as rollback_exc:
            log.warning("cli usage index: rollback failed (%s)", rollback_exc)
        return 0


__all__ = [
    "AGENTS",
    "AGENT_AGY",
    "AGENT_CLAUDE",
    "AGENT_CODEX",
    "AGENT_GROK",
    "AGENT_KIMI",
    "AGENT_OPENCODE",
    "COST_READER_FOR_HARNESS",
    "DB_NAME",
    "HARNESSES_WITHOUT_LOCAL_TRANSCRIPT",
    "CliRollup",
    "CliTurn",
    "IndexState",
    "RefreshResult",
    "entries",
    "rollups",
    "index_db_path",
    "index_state",
    "refresh",
]
