"""Provider surface for the dictation polish pass — two adapters, six families.

The one branch in this module
-----------------------------
``PolishFamily.transport`` is either ``"openai_chat"`` or ``"gemini"``. That is
a WIRE FORMAT, not a vendor: five of the six families below speak the
OpenAI-compatible chat schema and one speaks Google's. Nothing anywhere
branches on ``family.id`` — the id exists so a user can pin a preference and so
a log line can name who answered (AP-21: gate on capability, never on a
provider name or model id).

Adding a family is therefore a row in :data:`POLISH_FAMILIES` and nothing else.

Why the chain is credential-derived
-----------------------------------
:func:`resolve_polish_chain` returns the families the user actually holds a key
for, one entry per family, primary first. A depleted or unreachable provider
crosses to a DIFFERENT family; it never falls back to a second model in the same
one, because a rate-limited account is rate-limited for all of its models
(AP-22). When the user holds no key anywhere the chain is EMPTY, the caller
reports ``unavailable`` and delivers the raw transcript — byte-identical to the
behaviour before this feature existed. That empty-chain path is the whole
open-source contract: the maintainer's key must never be the thing that makes
the default safe (AP-23).

Why an on-device recognizer changes the chain
---------------------------------------------
Credentials alone are not enough to decide this, and the gap is a privacy
regression rather than a missing feature. Dictation is the most sensitive text
this application handles, and somebody who chose the on-device recognizer chose
it so their words would never leave the machine — the repo's documented
privacy-hybrid posture. If the polish chain asked only "what keys are there",
that user would start uploading every dictated sentence the moment they held a
cloud key for anything else, on an update, with nothing on screen to say so.

So :func:`resolve_polish_chain` asks one more question first
(:func:`stt_runs_on_device`), and when the recognizer transcribes here it offers
ONLY families whose endpoint is on this machine. An explicit ``polish_provider``
pin to a cloud family still wins: that is a decision the user made deliberately,
and honouring it is the difference between a safe default and a policy.

Why a credential is read once per change, not once per dictation
----------------------------------------------------------------
``jarvis.core.config.get_secret`` is not a cheap call — it reads the local file
store, calls the OS keyring and, on a miss, scans ``.env``. On a Linux desktop
with a locked keyring or a slow D-Bus Secret Service it can block for seconds.
The polish pass would otherwise pay that up to seven times to decide the chain
and AGAIN for whichever family it dials, the second of those on the event loop.
:data:`_SECRET_MEMO` therefore keeps one resolved value per slot, tagged with
``jarvis.core.config.secret_revision`` so an in-app save or delete invalidates
its own entry, and :func:`polish_chain_fingerprint` lets the caller reuse a
whole resolved chain until something that decides it actually changes. A read
that FAILS is remembered too, but only for :data:`_SECRET_FAILURE_TTL_S` — long
enough that one dictation cannot pay a blocking keyring twice, short enough
that unlocking it is picked up without a restart.

Why an endpoint that was not there is remembered
------------------------------------------------
The same argument applies one layer out. A family whose endpoint is on this
machine is refused instantly when nothing is listening on the port — but a
refusal per dictation is still a slice of a budget the design measures in
milliseconds, and a chain that pins the local family in front of a cloud one
pays it on every single delivery, forever, without the circuit breaker ever
noticing (the pass succeeds). :func:`note_family_failure` therefore records a
local endpoint that did not answer, and :func:`family_recently_unreachable`
keeps the walk from dialling it again for :data:`_LOCAL_REFUSAL_TTL_S`. Only
on-device families: a cloud outage is answered by crossing to another family
and, if it persists, by the breaker.

Import weight
-------------
``httpx`` and ``google-genai`` are imported inside client construction, which
happens on the first dictation — never at import time, never at boot (AP-26).
``jarvis.core.config`` (Pydantic, heavy) is likewise imported lazily inside the
credential lookup.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

#: The wire formats an adapter exists for. This — not a provider name — is the
#: only thing the code is allowed to branch on.
POLISH_TRANSPORTS: Final[tuple[str, ...]] = ("openai_chat", "gemini")

#: Hostnames that mean "this machine". A family whose endpoint resolves to one
#: of these never puts the user's words on a network — which is the property
#: the privacy rule in :func:`resolve_polish_chain` acts on.
_LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset(
    {"localhost", "127.0.0.1", "::1"}
)

#: How long an on-device endpoint that did not answer is left alone. Chosen
#: below the breaker's 120 s cooldown on purpose: somebody who reads
#: ``local_only``, starts their local model and dictates again should be
#: polished on the next attempt or the one after, not two minutes later.
_LOCAL_REFUSAL_TTL_S: Final[float] = 60.0

#: How long a FAILED credential read is left alone, per slot. Short, because
#: the failure it covers is "the keyring is locked right now" — a state the
#: user fixes by hand and expects to take effect. Long enough that the two
#: reads inside one dictation (decide the chain, build the client) cannot both
#: pay a keyring that blocks for seconds before raising.
_SECRET_FAILURE_TTL_S: Final[float] = 10.0


@dataclass(frozen=True, slots=True)
class PolishFamily:
    """One credential family the polish pass can reach.

    ``secret_candidates`` are keyring slot names in priority order, resolved
    through :func:`jarvis.core.config.get_secret` (keyring -> ENV -> ``.env`` ->
    local file), so a headless VPS with no Secret Service reaches exactly the
    same families as a desktop. An EMPTY tuple marks a keyless family (a local
    engine): it never enters the auto CLOUD order, but it can be pinned, and it
    is the whole chain when the recognizer itself runs on this machine — see
    :func:`resolve_polish_chain`.
    """

    id: str
    label: str
    transport: Literal["openai_chat", "gemini"]
    base_url: str
    secret_candidates: tuple[str, ...]
    default_model: str
    default_timeout_ms: int
    #: Provider card this family INHERITS its address from, or None for a
    #: hosted endpoint that never moves. A self-hosted server has exactly one
    #: address, and the user set it once on that provider's card; keeping a
    #: second copy here pinned to localhost meant that moving the server to
    #: another box silently left this feature talking to nothing. Data, not a
    #: name branch (AP-21).
    endpoint_provider: str | None = None

    @property
    def needs_key(self) -> bool:
        """Whether this family is reachable only with a credential."""
        return bool(self.secret_candidates)

    @property
    def effective_base_url(self) -> str:
        """The address this family is ACTUALLY reached at right now.

        A hosted family is its declared ``base_url``. A family that inherits
        (``endpoint_provider``) follows whatever server the user configured on
        that card — including one on another machine. Falls back to the
        declared address when the lookup cannot be made, which is the previous
        behaviour and never worse than it.
        """
        if not self.endpoint_provider:
            return self.base_url
        try:
            from jarvis.core import config as cfg  # noqa: PLC0415 — lazy (AP-26)
            from jarvis.plugins.brain.ollama import (  # noqa: PLC0415 — pure helpers
                default_server_root,
                normalize_server_root,
            )

            endpoint = cfg.resolve_provider_endpoint(
                self.endpoint_provider, vendor_default_base_url=default_server_root()
            )
            root = normalize_server_root(endpoint.base_url or default_server_root())
            return f"{root}/v1"
        except Exception:  # noqa: BLE001 — an unreadable config keeps the default
            log.debug(
                "polish: could not resolve the %s endpoint; using %s",
                self.endpoint_provider,
                self.base_url,
            )
            return self.base_url

    @property
    def runs_on_device(self) -> bool:
        """Whether this family's endpoint is on THIS machine.

        The privacy capability, and deliberately not spelled ``not
        needs_key``. Those two agree in today's table, but they answer
        different questions: "needs no credential" is about billing, while
        "the endpoint is a loopback address" is about whether the user's words
        travel over a network at all — and that is the only thing
        :func:`resolve_polish_chain` may act on when the recognizer itself is
        on-device. Read off the EFFECTIVE address: an Ollama server on another
        box in the house is keyless but not on-device, and claiming otherwise
        would be a privacy promise we do not keep.
        """
        host = (urlsplit(self.effective_base_url).hostname or "").strip().lower()
        return host in _LOOPBACK_HOSTS


#: The single source of truth for the polish tier, in auto-selection order.
#:
#: The order is the ranking from the design's provider study: time-to-first-token
#: first, cost second, and — decisively for the first entry — whether the key is
#: likely to be present already. Groq leads because it is the shipped default STT
#: provider, so on most installs the feature turns itself on with no new account;
#: OpenRouter sits late because one OpenRouter key reaches every family, which
#: makes it the best universal floor rather than the best primary.
#:
#: Ollama is last and keyless: it is the offline floor for someone who wants no
#: cloud call at all, but it requires the opt-in local stack, so it never enters
#: the auto CLOUD order (a chain that dialled localhost on every dictation would
#: spend part of the latency budget on a connection refusal for everyone who
#: never ran a local model). It is used when pinned, and it is the whole chain
#: when the configured recognizer also runs on this machine — and in both of
#: those cases a refusal is remembered (:func:`note_family_failure`), so the
#: cost is paid once a minute rather than on every dictation.
POLISH_FAMILIES: Final[tuple[PolishFamily, ...]] = (
    PolishFamily(
        id="groq",
        label="Groq",
        transport="openai_chat",
        base_url="https://api.groq.com/openai/v1",
        secret_candidates=("groq_api_key",),
        # Groq's own catalog rates this at roughly twice the throughput of the
        # Llama 3.1 8B that used to sit here, and polish lives or dies on
        # latency: the budget above is 1.2 s for the whole call.
        default_model="openai/gpt-oss-20b",
        default_timeout_ms=1200,
    ),
    PolishFamily(
        id="cerebras",
        label="Cerebras",
        transport="openai_chat",
        base_url="https://api.cerebras.ai/v1",
        secret_candidates=("cerebras_api_key",),
        # The only model Cerebras still lists as production (checked
        # 2026-08-09). The previous default, llama-3.3-70b, is no longer on
        # that list — a default nobody serves is a 404 at dictation time.
        default_model="gpt-oss-120b",
        default_timeout_ms=1200,
    ),
    PolishFamily(
        id="gemini",
        label="Google Gemini",
        transport="gemini",
        base_url="https://generativelanguage.googleapis.com",
        # The same AI-Studio slots the Gemini brain/TTS/STT already read, so a
        # Gemini-only downloader needs no second credential.
        secret_candidates=(
            "gemini_api_key",
            "google_aistudio_api_key",
            "google_api_key",
        ),
        default_model="gemini-3.1-flash-lite",
        default_timeout_ms=1500,
    ),
    PolishFamily(
        id="openai",
        label="OpenAI",
        transport="openai_chat",
        base_url="https://api.openai.com/v1",
        secret_candidates=("openai_api_key",),
        default_model="gpt-4.1-nano",
        default_timeout_ms=1500,
    ),
    PolishFamily(
        id="openrouter",
        label="OpenRouter",
        transport="openai_chat",
        base_url="https://openrouter.ai/api/v1",
        secret_candidates=("openrouter_api_key",),
        # Current small flash-class model on OpenRouter's live catalog
        # (checked 2026-08-09), replacing a Llama 3.1 build from 2024.
        default_model="qwen/qwen3.7-flash",
        default_timeout_ms=1500,
    ),
    PolishFamily(
        id="ollama",
        label="Ollama (local)",
        transport="openai_chat",
        # Declared address is the vendor default; the effective one follows the
        # Ollama card, so a server moved to another machine keeps working.
        base_url="http://localhost:11434/v1",
        secret_candidates=(),
        default_model="qwen3.5:4b",
        default_timeout_ms=3000,
        endpoint_provider="ollama",
    ),
)

_FAMILY_BY_ID: Final[dict[str, PolishFamily]] = {f.id: f for f in POLISH_FAMILIES}


class PolishProviderError(RuntimeError):
    """A provider call failed in a way the caller may cross a family for.

    Carries the HTTP status when there was one, so the caller can tell a
    depleted account (402/429) from a broken credential (401) in a log line
    without re-parsing the message. The message itself stays English.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class PolishClient(Protocol):
    """What the orchestrator needs from a provider: one bounded completion."""

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_output_tokens: int,
        temperature: float,
        timeout_s: float,
    ) -> str | None:
        """Return the model's text, or ``None`` when it produced nothing."""
        ...


def family_by_id(family_id: object) -> PolishFamily | None:
    """Look a family up by its id; ``None`` for anything unknown."""
    return _FAMILY_BY_ID.get(str(family_id or "").strip().lower())


def family_has_key(family: PolishFamily) -> bool:
    """Whether this host holds a usable credential for *family*.

    A keyless family answers ``True``: there is nothing to hold. Reachability is
    a separate question and is answered by actually calling it — probing a local
    engine here would put a socket connect on the dictation path.
    """
    if not family.needs_key:
        return True
    return _first_secret(family.secret_candidates) is not None


#: One resolved credential per keyring slot, tagged with that slot's revision
#: at the moment it was read. At most seven entries, each overwritten in place.
#: See the module docstring for why a repeat read is worth avoiding.
_SECRET_MEMO: dict[str, tuple[int, str | None]] = {}

#: Slots whose lookup RAISED, as ``slot -> (revision, monotonic deadline)``.
#: Separate from :data:`_SECRET_MEMO` because it expires and that one does not:
#: a resolved value stays true until the revision moves, while a failure is a
#: statement about the keyring's mood a minute ago.
_SECRET_FAILURES: dict[str, tuple[int, float]] = {}


def reset_credential_cache() -> None:
    """Forget every memoised credential, resolved or failed.

    Two real callers: :func:`jarvis.dictation.polish.reset_polish_state`, so a
    user who just repaired a key is never answered from a read taken before the
    repair, and tests, which fake ``get_secret`` and must not inherit the
    previous test's answer through a revision counter that never moved.
    """
    _SECRET_MEMO.clear()
    _SECRET_FAILURES.clear()


def _lookup_failed_recently(slot: str, revision: int) -> bool:
    """Whether reading *slot* raised so recently that retrying is pointless.

    The entry is dropped as soon as it is stale, so the memo can only ever
    delay a retry by :data:`_SECRET_FAILURE_TTL_S` — and an in-app save or
    delete moves the revision, which invalidates it immediately. Between them
    those two are the property this must not break: a repaired credential is
    picked up without a restart.
    """
    entry = _SECRET_FAILURES.get(slot)
    if entry is None:
        return False
    failed_revision, deadline = entry
    if failed_revision != revision or time.monotonic() >= deadline:
        _SECRET_FAILURES.pop(slot, None)
        return False
    return True


def _first_secret(candidates: Sequence[str]) -> str | None:
    """First non-empty credential among *candidates*, or ``None``.

    ``jarvis.core.config`` is imported here rather than at module scope because
    it pulls Pydantic and the whole config model; this module must stay free to
    import from anywhere (AP-26).

    Every read goes through :data:`_SECRET_MEMO`, because the same slot is
    asked for at least twice per dictation — once to decide the chain, once to
    build the client that dials — and the second of those happens on the event
    loop inside :func:`build_polish_client`. ``secret_revision`` is the signal
    provider instances already use to notice a replaced key: an in-app save or
    delete bumps it and the entry misses; a credential edited outside the app
    needs a restart either way, which is the contract that counter documents.

    A read that RAISES is memoised too, but only briefly
    (:func:`_lookup_failed_recently`). A locked keyring that fails fast costs
    nothing to ask twice; the one this exists for is the keyring that BLOCKS
    for seconds and then raises, which is the same host the whole off-the-loop
    design was written for and which would otherwise pay that wait twice inside
    one dictation and again on the next.
    """
    from jarvis.core import config as _cfg

    for slot in candidates:
        revision = _cfg.secret_revision(slot)
        cached = _SECRET_MEMO.get(slot)
        if cached is not None and cached[0] == revision:
            value = cached[1]
        elif _lookup_failed_recently(slot, revision):
            # The same lookup raised moments ago and nothing has changed since
            # (an in-app write would have moved the revision). Skipping it is
            # what keeps a blocking keyring from being paid twice per pass; it
            # was already reported when it failed.
            continue
        else:
            try:
                value = _cfg.get_secret(slot)
            except Exception as exc:  # noqa: BLE001 — a locked keyring must not break dictation
                _SECRET_FAILURES[slot] = (
                    revision,
                    time.monotonic() + _SECRET_FAILURE_TTL_S,
                )
                log.debug("polish credential lookup failed for %r: %s", slot, exc)
                continue
            # Written without a lock on purpose: the sweep runs in a worker
            # thread while the client build reads from the event loop, the dict
            # assignment is atomic, and the worst a racing reader can see is the
            # previous entry — one extra read, never a wrong answer.
            _SECRET_MEMO[slot] = (revision, value)
        if value:
            return value
    return None


#: On-device endpoints that did not answer, as ``family id -> monotonic
#: deadline``. One entry per local family, overwritten in place; nothing else
#: is remembered, because an endpoint that ANSWERED and refused the request is
#: a different fact (see :func:`note_family_failure`).
_LOCAL_REFUSALS: dict[str, float] = {}


def reset_reachability_cache() -> None:
    """Forget which on-device endpoints were unreachable.

    Called by :func:`jarvis.dictation.polish.reset_polish_state`: someone who
    just started their local model, or who changed the setting that decides
    which one is dialled, must not be told for another minute that nothing is
    listening.
    """
    _LOCAL_REFUSALS.clear()


def note_family_failure(family: PolishFamily, exc: BaseException) -> None:
    """Remember an ON-DEVICE endpoint that was not there at all.

    Two narrow conditions, because the memo skips a whole family and a wrong
    entry costs the user a polish pass they could have had:

    * only a family whose endpoint is on this machine. A cloud family that is
      unreachable may be a network blip; crossing to the next family already
      answers it, and the circuit breaker answers a persistent one.
    * only a failure with NO HTTP status. A status means something is
      listening and merely refused this request — a model that was never
      pulled, a 500 — and dialling it again next time is both cheap and likely
      to work once the user fixes it.

    Anything else (an SDK exception, a transport error) is treated as "nothing
    answered", which is the state this is for.
    """
    if not family.runs_on_device:
        return
    if isinstance(exc, PolishProviderError) and exc.status is not None:
        return
    _LOCAL_REFUSALS[family.id] = time.monotonic() + _LOCAL_REFUSAL_TTL_S
    log.debug(
        "polish family %r is on this machine and did not answer (%s); it will "
        "be skipped for %.0f s rather than dialled on every dictation.",
        family.id,
        exc,
        _LOCAL_REFUSAL_TTL_S,
    )


def family_recently_unreachable(family: PolishFamily) -> bool:
    """Whether *family* refused so recently that dialling it again is waste.

    The entry is dropped the moment it goes stale, so this can only ever delay
    a retry by :data:`_LOCAL_REFUSAL_TTL_S`, and
    :func:`reset_reachability_cache` drops it on any settings change.
    """
    deadline = _LOCAL_REFUSALS.get(family.id)
    if deadline is None:
        return False
    if time.monotonic() >= deadline:
        _LOCAL_REFUSALS.pop(family.id, None)
        return False
    return True


def stt_runs_on_device() -> bool:
    """Whether the CONFIGURED recognizer transcribes on this machine.

    Asked as a capability, never as a provider name (AP-21):
    :func:`jarvis.plugins.stt.provider_runs_on_device` puts the question to the
    recognizer plugin itself, so a second on-device engine joins this rule by
    declaring ``runs_on_device = True`` on its provider class and changing
    nothing here. What that function cannot do is guess for a third-party
    plugin that declares nothing — it answers ``False`` there, and so does
    this. The promise is "ask the plugin", not "be right about every plugin".

    **Fails closed.** A host whose configuration cannot be read is treated as
    on-device, because the two mistakes are not symmetric. Guessing "local"
    costs a formatting pass the user can switch back on with one explicit pin.
    Guessing "cloud" uploads dictated sentences from someone who may have
    chosen a local recognizer specifically to prevent that — and they would
    never see it happen.

    Both imports are function-local: this module is imported by the provider
    catalogue, and neither the config model nor the STT plugin package belongs
    on that path (AP-26). The call reads the config file, so it belongs off the
    event loop — see :func:`polish_chain_fingerprint`.
    """
    from jarvis.core import config as _cfg
    from jarvis.plugins.stt import provider_runs_on_device

    try:
        provider = str(getattr(_cfg.load_config().stt, "provider", "") or "")
    except Exception as exc:  # noqa: BLE001 — see the fail-closed note above
        log.warning(
            "dictation polish could not read the configured recognizer (%s); "
            "treating it as on-device and keeping the polish pass local.",
            exc,
        )
        return True
    return provider_runs_on_device(provider)


def polish_chain_fingerprint(cfg: Any) -> tuple[Any, ...]:
    """A cheap value that changes whenever :func:`resolve_polish_chain` could.

    Resolving the chain is expensive and blocking: up to seven credential slots
    plus a config read, all of it after the microphone has already closed. The
    caller therefore resolves it once per settings change and reuses the answer
    — this is the value it compares. Computing it must stay cheap enough to run
    on the event loop, so it touches only a dict lookup per slot and one
    ``stat``.

    Three inputs, because three things can change the answer: the user's pin,
    the credentials they hold (``secret_revision`` counts every in-app write),
    and the identity of the config file — which is where ``[stt].provider``
    lives, and a switch to an on-device recognizer has to take effect on the
    next dictation rather than the next restart.
    """
    from jarvis.core import config as _cfg

    pin = str(getattr(cfg, "polish_provider", "auto") or "auto").strip().lower()
    revisions = tuple(
        _cfg.secret_revision(slot)
        for family in POLISH_FAMILIES
        for slot in family.secret_candidates
    )
    identity: tuple[int, int] | None = None
    try:
        info = _cfg.resolve_config_path().stat()
        identity = (info.st_mtime_ns, info.st_size)
    except OSError as exc:
        # A fresh clone has no config file at all, and an unreadable one is
        # somebody else's error to report. Either way the only consequence is
        # that the sweep runs again — slow, never wrong.
        log.debug("polish chain fingerprint has no config identity: %s", exc)
    return (pin, revisions, identity)


def resolve_polish_chain(cfg: Any) -> tuple[PolishFamily, ...]:
    """Key-aware, family-crossing, honest-degrading provider order (AP-22).

    Returns the families this host can actually use, primary first, each from a
    DIFFERENT credential family. An empty tuple means there is nothing to try —
    the caller then reports ``unavailable`` and delivers the raw text, which is
    exactly today's behaviour and must stay byte-identical (AP-23).

    Two rules decide it, in this order.

    **1. An on-device recognizer keeps the pass on-device.** When
    :func:`stt_runs_on_device` says the configured recognizer transcribes here,
    only families whose endpoint is on this machine are offered. The ``auto``
    default NEVER crosses to a cloud family, however many keys the user holds
    for other things — because the user picked a local recognizer so their
    words would stay local, and a chain derived from credentials alone would
    have started uploading every dictated sentence on an update, silently.

    **2. Everything else is credential-derived.**
    ``[dictation].polish_provider`` is a user PIN, not a code branch: a
    recognised id moves to the front and the remaining keyed families follow it
    as cross-family fallbacks, so pinning a preference never costs the user
    their resilience. A pin to a CLOUD family also overrides rule 1 — an
    explicit choice is a decision, and once it is made the ordinary AP-22
    resilience applies to it. It only counts while it is USABLE, though: a pin
    whose key has gone is not an opt-in, so it degrades back to the on-device
    chain rather than becoming a licence to upload. An unrecognised pin is
    ignored in favour of the auto order rather than emptying the chain — a typo
    in a config file must not silently disable a feature the user asked for.

    A keyless on-device family never enters the auto CLOUD order, because a
    chain that dialled localhost on every dictation would spend part of the
    latency budget on a connection refusal for the large majority of installs
    that never ran a local model. It is reachable by a pin, and it is the whole
    chain under rule 1 — where the alternative is not a faster provider but no
    polish at all.
    """
    pin = str(getattr(cfg, "polish_provider", "auto") or "auto").strip().lower()
    pinned = family_by_id(pin) if pin not in ("", "auto") else None
    if pinned is None and pin not in ("", "auto"):
        log.debug(
            "polish provider pin %r is not a known family; using the auto order.", pin
        )

    if stt_runs_on_device():
        opted_in = (
            pinned is not None
            and not pinned.runs_on_device
            and family_has_key(pinned)
        )
        if not opted_in:
            local = tuple(f for f in POLISH_FAMILIES if f.runs_on_device)
            log.debug(
                "the configured recognizer transcribes on this machine, so the "
                "polish chain stays on it too: %s.",
                ", ".join(f.id for f in local) or "<no on-device family>",
            )
            return local

    chain: list[PolishFamily] = []
    if pinned is not None and family_has_key(pinned):
        chain.append(pinned)
    for family in POLISH_FAMILIES:
        if family in chain:
            continue
        # Auto-selection is credential-driven, so a keyless local family is
        # skipped here; it is reachable only as an explicit pin (above).
        if not family.needs_key:
            continue
        if family_has_key(family):
            chain.append(family)
    return tuple(chain)


def resolve_model(family: PolishFamily, cfg: Any, *, primary_id: str) -> str:
    """The model id to use for *family* on this call.

    ``[dictation].polish_model`` applies to the PRIMARY family only. A model id
    is family-specific — ``llama-3.1-8b-instant`` means nothing to Gemini — so
    carrying the user's pinned model across a fallback would turn a recoverable
    outage into a guaranteed 404.
    """
    if family.id == primary_id:
        pinned = str(getattr(cfg, "polish_model", "") or "").strip()
        if pinned:
            return pinned
    return family.default_model


# --------------------------------------------------------------------------- #
# Transport adapters
# --------------------------------------------------------------------------- #


class _SharedHttpClient:
    """One keep-alive ``httpx.AsyncClient`` for every OpenAI-compatible family.

    A fresh client per dictation forces a fresh TCP + TLS handshake, which on a
    1200 ms budget is a meaningful slice of it. The client is rebound whenever
    the running event loop changes so a cached client is never reused across
    loops (each pytest-asyncio test runs in its own loop, and a reused client
    would raise ``RuntimeError: Event loop is closed``).
    """

    __slots__ = ("_client", "_loop")

    def __init__(self) -> None:
        self._client: Any | None = None
        self._loop: Any | None = None

    def get(self) -> Any:
        import asyncio

        import httpx

        loop = asyncio.get_running_loop()
        client = self._client
        if client is None or self._loop is not loop or client.is_closed:
            client = httpx.AsyncClient()
            self._client = client
            self._loop = loop
        return client

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        self._loop = None
        if client is not None and not client.is_closed:
            try:
                await client.aclose()
            except Exception as exc:  # noqa: BLE001 — teardown must never raise
                log.debug("polish HTTP client close failed: %s", exc)


_HTTP = _SharedHttpClient()


async def aclose_shared_client() -> None:
    """Release the pooled HTTP client. Safe to call repeatedly."""
    await _HTTP.aclose()


class OpenAIChatPolishClient:
    """Adapter for every family speaking the OpenAI chat-completions schema."""

    __slots__ = ("_family", "_model", "_api_key")

    def __init__(self, family: PolishFamily, *, model: str, api_key: str | None) -> None:
        self._family = family
        self._model = model
        self._api_key = api_key

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_output_tokens: int,
        temperature: float,
        timeout_s: float,
    ) -> str | None:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_output_tokens,
            "temperature": temperature,
            "stream": False,
            # A polish pass is a deterministic rewrite, so OpenAI-compatible
            # transports should spend their budget on visible text rather than
            # hidden reasoning. This is an optional wire-level intent, not a
            # provider/model capability claim: a 400 naming the field removes
            # it for the single schema-negotiation retry below.
            "reasoning_effort": "low",
        }
        data = await self._post(payload, timeout_s=timeout_s)
        choices = data.get("choices") or []
        if not choices:
            return None
        message = (choices[0] or {}).get("message") or {}
        text = str(message.get("content") or "").strip()
        return text or None

    async def _post(self, payload: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        """POST the payload, retrying ONCE against a schema-shape rejection.

        OpenAI-compatible endpoints can rename ``max_tokens``, pin
        ``temperature``, or reject the optional low-reasoning intent. These
        show up as a 400 naming the offending field, so the retry is driven by
        what the SERVER said rather than by a provider/model allowlist (AP-21).
        """
        import httpx

        url = f"{self._family.effective_base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        client = _HTTP.get()

        for attempt in (0, 1):
            try:
                response = await client.post(
                    url, json=payload, headers=headers, timeout=timeout_s
                )
            except httpx.HTTPError as exc:
                raise PolishProviderError(
                    f"{self._family.label} polish request failed: {exc}"
                ) from exc
            if response.status_code < 400:
                return response.json()
            body = _safe_body(response)
            if attempt == 0 and response.status_code == 400:
                adjusted = _relax_payload(payload, body)
                if adjusted is not None:
                    payload = adjusted
                    continue
            raise PolishProviderError(
                f"{self._family.label} polish request returned "
                f"HTTP {response.status_code}: {body[:200]}",
                status=response.status_code,
                retry_after=_retry_after(response),
            )
        # Unreachable: the loop either returns or raises on both attempts.
        raise PolishProviderError(f"{self._family.label} polish request failed")


def _safe_body(response: Any) -> str:
    try:
        return str(response.text or "")
    except Exception:  # noqa: BLE001 — a body we cannot read is not a new failure
        return ""


def _retry_after(response: Any) -> float | None:
    try:
        value = response.headers.get("retry-after")
    except Exception:  # noqa: BLE001
        return None
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def _relax_payload(payload: dict[str, Any], body: str) -> dict[str, Any] | None:
    """Rewrite the one field the server complained about, or ``None``.

    Returns a NEW dict so a retry can never accumulate half-applied edits.
    """
    lowered = body.lower()
    adjusted = dict(payload)
    changed = False
    if "max_completion_tokens" in lowered and "max_tokens" in adjusted:
        adjusted["max_completion_tokens"] = adjusted.pop("max_tokens")
        changed = True
    if "temperature" in lowered and "temperature" in adjusted:
        adjusted.pop("temperature")
        changed = True
    if "reasoning_effort" in lowered and "reasoning_effort" in adjusted:
        adjusted.pop("reasoning_effort")
        changed = True
    return adjusted if changed else None


#: Shortest request deadline the generate-content endpoint accepts. Anything
#: under it is rejected BEFORE the model runs, with
#: ``400 INVALID_ARGUMENT: Manually set deadline Ns is too short. Minimum
#: allowed deadline is 10s.`` — so a polish budget below this cannot buy a
#: faster answer, only a guaranteed failure.
_GEMINI_MIN_DEADLINE_S = 10.0


class GeminiPolishClient:
    """Adapter for Google's generate-content schema."""

    __slots__ = ("_family", "_model", "_api_key", "_client")

    def __init__(self, family: PolishFamily, *, model: str, api_key: str) -> None:
        self._family = family
        self._model = model
        self._api_key = api_key
        self._client: Any = None

    def _ensure_client(self, timeout_s: float) -> Any:
        if self._client is None:
            # Routed builder: AI Studio or Vertex express, decided per key.
            from jarvis.core.google_genai import build_genai_client

            # google-genai forces ``timeout=None`` on its own httpx client, so
            # an explicit http_options timeout is the ONLY thing below the
            # caller's wait_for that can stop a hung request from holding a
            # connection open after we have already given up on it.
            #
            # It is deliberately NOT the polish budget. This family's budget is
            # 1.5 s, and sending that as the request deadline made the server
            # reject every single call with a 400 before the model ran: measured
            # on the live log, Gemini failed 56 times and succeeded 0 times, so a
            # user who pinned it never once got the provider they chose — every
            # dictation crossed silently to the next family (AP-31: a switch
            # whose value is ignored). The deadline is what the SERVER is willing
            # to accept; the caller's ``wait_for`` is what actually bounds the
            # wait, and it is unchanged. Giving up at 1.5 s and letting the
            # socket close on its own is strictly better than not asking at all.
            self._client = build_genai_client(
                self._api_key,
                http_options={
                    "timeout": int(max(timeout_s, _GEMINI_MIN_DEADLINE_S) * 1000)
                },
            )
        return self._client

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_output_tokens: int,
        temperature: float,
        timeout_s: float,
    ) -> str | None:
        try:
            # First use builds the client: google-genai import + (for an AQ.
            # key) the one-time routing probe — neither belongs on the loop.
            client = await asyncio.to_thread(self._ensure_client, timeout_s)
            from google.genai import types as genai_types

            response = await client.aio.models.generate_content(
                model=self._model,
                contents=[user],
                config=genai_types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                ),
            )
        except Exception as exc:  # noqa: BLE001 — the SDK raises its own hierarchy
            raise PolishProviderError(
                f"{self._family.label} polish request failed: {exc}"
            ) from exc
        text = str(getattr(response, "text", "") or "").strip()
        return text or None


def build_polish_client(family: PolishFamily, *, model: str) -> PolishClient | None:
    """Construct the adapter for *family*, or ``None`` when it is unusable.

    ``None`` (rather than an exception) is the answer for "no credential" and
    for "the SDK this transport needs is not installed", because both are
    ordinary states on some install, and the caller's correct response to both
    is identical: try the next family.
    """
    api_key = _first_secret(family.secret_candidates) if family.needs_key else None
    if family.needs_key and not api_key:
        return None
    try:
        if family.transport == "gemini":
            return GeminiPolishClient(family, model=model, api_key=str(api_key))
        return OpenAIChatPolishClient(family, model=model, api_key=api_key)
    except Exception as exc:  # noqa: BLE001 — an unbuildable family is just the next one
        log.warning(
            "polish client for %r not buildable (%s); trying the next family.",
            family.id,
            exc.__class__.__name__,
        )
        return None


__all__ = [
    "POLISH_FAMILIES",
    "POLISH_TRANSPORTS",
    "GeminiPolishClient",
    "OpenAIChatPolishClient",
    "PolishClient",
    "PolishFamily",
    "PolishProviderError",
    "aclose_shared_client",
    "build_polish_client",
    "family_by_id",
    "family_has_key",
    "family_recently_unreachable",
    "note_family_failure",
    "polish_chain_fingerprint",
    "reset_credential_cache",
    "reset_reachability_cache",
    "resolve_model",
    "resolve_polish_chain",
    "stt_runs_on_device",
]
