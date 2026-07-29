"""The polish pass must not undo the user's choice of an on-device recognizer.

The defect this file guards against is the worst kind: invisible, and a
regression rather than a missing feature. ``[dictation].polish`` ships ON and
its chain used to be derived from credentials ALONE — so somebody who picked the
local recognizer specifically so their dictation would never leave the machine,
but who holds a cloud key for the brain, would have started uploading every
dictated sentence on an update. Nothing on screen would have said so.

Three properties are pinned here, and each one fails silently in production if
it breaks:

* **the privacy rule** — an on-device recognizer keeps the polish pass
  on-device, an explicit pin still wins, and a cloud recognizer is untouched;
* **the honest status** — when nothing local answers, the pass says
  ``local_only`` and hands back the raw transcript, rather than reading like a
  cloud outage;
* **where the work happens** — the credential sweep runs OFF the event loop and
  once per settings change, because on a host with a locked keyring or a slow
  D-Bus Secret Service it blocks for seconds, and the loop it would block also
  carries the microphone, the WebSocket and the Jarvis Bar.

Credentials are faked by replacing ``jarvis.core.config.get_secret`` — the real
lookup — so the slot NAMES in ``POLISH_FAMILIES`` stay part of what is pinned.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core import config as jarvis_config
from jarvis.dictation import polish, polish_client
from jarvis.dictation.polish import POLISH_STATUSES, polish_transcript
from jarvis.dictation.polish_client import (
    POLISH_FAMILIES,
    PolishFamily,
    PolishProviderError,
    resolve_polish_chain,
    stt_runs_on_device,
)

GROQ: PolishFamily = POLISH_FAMILIES[0]
ON_DEVICE: PolishFamily = next(f for f in POLISH_FAMILIES if f.runs_on_device)

RAW = "so we should probably move the meeting to the morning and tell the team"
POLISHED = "So we should probably move the meeting to the morning, and tell the team."


@dataclass
class _Cfg:
    """A stand-in for ``DictationConfig`` carrying only the polish settings."""

    polish: bool = True
    polish_provider: str = "auto"
    polish_model: str = ""
    polish_timeout_ms: int = 1200
    polish_max_input_chars: int = 4000
    polish_min_words: int = 4


@pytest.fixture(autouse=True)
def _forget_everything_between_tests() -> None:
    """The breaker, the cached chain and the memoised credentials all persist
    for the life of the process on purpose. A test that inherited any of them
    would be reading the previous test's host."""
    polish.reset_polish_state()


def _with_keys(monkeypatch: pytest.MonkeyPatch, slots: dict[str, str]) -> None:
    """Pretend this host holds exactly *slots* and nothing else."""

    def _fake_get_secret(key: str, env_fallback: str | None = None) -> str | None:
        return slots.get(key)

    monkeypatch.setattr(jarvis_config, "get_secret", _fake_get_secret)


def _with_recognizer(monkeypatch: pytest.MonkeyPatch, *, on_device: bool) -> None:
    """Pretend the configured recognizer does or does not transcribe here."""
    monkeypatch.setattr(
        polish_client, "stt_runs_on_device", lambda: on_device
    )


def _ids(chain: tuple[PolishFamily, ...]) -> list[str]:
    return [family.id for family in chain]


# --------------------------------------------------------------------------- #
# What "on-device" means — a capability, never a provider name
# --------------------------------------------------------------------------- #


def test_on_device_is_decided_by_the_endpoint_not_by_a_family_name() -> None:
    """AP-21. The question the privacy rule asks is "do the user's words leave
    this machine", and the only honest answer is the address they are sent to."""
    assert ON_DEVICE.runs_on_device
    assert "localhost" in ON_DEVICE.base_url or "127.0.0.1" in ON_DEVICE.base_url
    for family in POLISH_FAMILIES:
        if family is ON_DEVICE:
            continue
        assert not family.runs_on_device, family.id
        assert family.base_url.startswith("https://"), family.id


def test_the_family_table_always_offers_somewhere_local_to_go() -> None:
    """The privacy rule answers with "every on-device family". If that set were
    ever empty, an on-device install would fall through the rule into the
    generic "no credential" path and be told the wrong thing about why nothing
    was polished."""
    assert [f.id for f in POLISH_FAMILIES if f.runs_on_device]


def _configured_recognizer(provider: str) -> Any:
    """A ``load_config`` stand-in whose ``[stt].provider`` is *provider*."""
    return lambda: SimpleNamespace(stt=SimpleNamespace(provider=provider))


def test_the_recognizer_probe_asks_the_stt_layer_for_the_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ONE verdict, taken from the layer that owns recognizers. A polish pass
    with its own opinion about which engines are local would drift the moment a
    plugin changed, and the drift is invisible: it decides whether dictated
    text is uploaded."""
    from jarvis.plugins.stt import provider_runs_on_device, stt_family_id

    assert provider_runs_on_device("faster-whisper") is True
    assert provider_runs_on_device("groq-api") is False
    # The credential-family map keeps agreeing with it, because that is what
    # keeps an on-device engine out of the cross-family fallback chain.
    assert stt_family_id("faster-whisper") == "local"

    monkeypatch.setattr(
        jarvis_config, "load_config", _configured_recognizer("faster-whisper")
    )
    assert stt_runs_on_device() is True

    monkeypatch.setattr(
        jarvis_config, "load_config", _configured_recognizer("groq-api")
    )
    assert stt_runs_on_device() is False


def test_a_second_on_device_recognizer_only_has_to_declare_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AP-21, and the property the comment on the probe promises. The question
    goes to the recognizer PLUGIN — a class that says it transcribes here is
    believed, whatever it is called — so a second on-device engine reaches the
    privacy rule without a name being added to anything in the dictation code.

    The old shape hardcoded one engine name, which meant a second on-device
    recognizer would have been read as a CLOUD one: the unsafe direction, on
    the one predicate where the mistake uploads the user's words.
    """
    from jarvis.plugins import stt as stt_pkg

    class _DeclaredLocal:
        """A third-party recognizer that transcribes on this machine."""

        name = "whisper-cpp"
        runs_on_device = True

    class _SaysNothing:
        """A third-party recognizer that declares nothing — read as remote."""

        name = "mystery-stt"

    plugins: dict[str, type] = {
        "whisper-cpp": _DeclaredLocal,
        "mystery-stt": _SaysNothing,
    }
    monkeypatch.setattr(stt_pkg, "_load_provider_class", plugins.get)
    stt_pkg.provider_runs_on_device.cache_clear()
    try:
        assert stt_pkg.provider_runs_on_device("whisper-cpp") is True
        assert stt_pkg.provider_runs_on_device("mystery-stt") is False
        assert stt_pkg.stt_family_id("whisper-cpp") == "local"

        # And the polish privacy rule follows it with no change of its own.
        monkeypatch.setattr(
            jarvis_config, "load_config", _configured_recognizer("whisper-cpp")
        )
        assert stt_runs_on_device() is True
        _with_keys(monkeypatch, {"groq_api_key": "gsk-test"})
        assert _ids(resolve_polish_chain(_Cfg())) == [ON_DEVICE.id]
    finally:
        # The answer is cached for the life of the process; a fake plugin map
        # must not be the answer the next test gets.
        stt_pkg.provider_runs_on_device.cache_clear()


def test_a_configuration_we_cannot_read_is_treated_as_on_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed. The two mistakes are not symmetric: guessing "local" costs a
    formatting pass the user can switch back on with one pin, while guessing
    "cloud" uploads dictation from somebody who may have chosen a local
    recognizer precisely to stop that."""

    def _boom() -> Any:
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(jarvis_config, "load_config", _boom)

    assert stt_runs_on_device() is True


# --------------------------------------------------------------------------- #
# The rule itself
# --------------------------------------------------------------------------- #


def test_a_local_recognizer_with_a_cloud_key_never_reaches_a_cloud_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE regression. Local recognizer, a Groq key present for something else,
    ``polish = true`` and ``polish_provider = "auto"`` — the shipped defaults.
    Nothing in this chain may leave the machine."""
    _with_recognizer(monkeypatch, on_device=True)
    _with_keys(monkeypatch, {"groq_api_key": "gsk-test", "openai_api_key": "sk-test"})

    chain = resolve_polish_chain(_Cfg())

    assert chain
    assert all(family.runs_on_device for family in chain), _ids(chain)
    assert _ids(chain) == [ON_DEVICE.id]


def test_the_same_install_does_cross_when_the_user_pins_a_cloud_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit pin is a decision, and a decision must be honoured — the
    difference between a safe default and a policy. Once the user has opted in,
    the ordinary AP-22 resilience applies to their choice too."""
    _with_recognizer(monkeypatch, on_device=True)
    _with_keys(monkeypatch, {"groq_api_key": "gsk-test", "openai_api_key": "sk-test"})

    chain = resolve_polish_chain(_Cfg(polish_provider="openai"))

    assert _ids(chain)[0] == "openai"
    assert "groq" in _ids(chain)


def test_a_cloud_pin_whose_key_is_gone_falls_back_to_the_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pin only counts while it is usable. Degrading a dead cloud pin onto
    WHATEVER other cloud key happens to be lying around would turn a revoked
    credential into an unannounced change of destination."""
    _with_recognizer(monkeypatch, on_device=True)
    _with_keys(monkeypatch, {"groq_api_key": "gsk-test"})

    chain = resolve_polish_chain(_Cfg(polish_provider="openai"))

    assert _ids(chain) == [ON_DEVICE.id]


def test_pinning_the_local_family_on_a_local_recognizer_adds_no_cloud_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Off a local recognizer, pinning the local model normally earns cloud
    fallbacks (AP-22). Here it must not: crossing would be exactly the upload
    the user's whole setup is arranged to avoid."""
    _with_recognizer(monkeypatch, on_device=True)
    _with_keys(monkeypatch, {"groq_api_key": "gsk-test"})

    chain = resolve_polish_chain(_Cfg(polish_provider=ON_DEVICE.id))

    assert _ids(chain) == [ON_DEVICE.id]


def test_a_cloud_recognizer_install_is_completely_unaffected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other side of the gate. Somebody already sending their audio to a
    cloud recognizer loses nothing here — same order, same families, and the
    local engine still stays out of the auto chain."""
    _with_recognizer(monkeypatch, on_device=False)
    _with_keys(monkeypatch, {"groq_api_key": "g", "openai_api_key": "o"})

    chain = resolve_polish_chain(_Cfg())

    assert _ids(chain) == ["groq", "openai"]
    assert ON_DEVICE.id not in _ids(chain)


def test_a_local_recognizer_and_no_keys_at_all_still_stays_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The privacy rule is not a consequence of having no keys — it holds with
    none and with several, which is what makes it a rule rather than a
    coincidence of the fresh-install state."""
    _with_recognizer(monkeypatch, on_device=True)
    _with_keys(monkeypatch, {})

    assert _ids(resolve_polish_chain(_Cfg())) == [ON_DEVICE.id]


# --------------------------------------------------------------------------- #
# The honest status
# --------------------------------------------------------------------------- #


@dataclass
class _FakeClient:
    reply: str | None = None
    raises: BaseException | None = None
    threads: list[int] = field(default_factory=list)

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_output_tokens: int,
        temperature: float,
        timeout_s: float,
    ) -> str | None:
        self.threads.append(threading.get_ident())
        if self.raises is not None:
            raise self.raises
        return self.reply


def _wire_chain(
    monkeypatch: pytest.MonkeyPatch,
    chain: tuple[PolishFamily, ...],
    client: Any,
) -> None:
    monkeypatch.setattr(polish, "resolve_polish_chain", lambda cfg: chain)
    monkeypatch.setattr(polish, "build_polish_client", lambda family, *, model: client)


def test_local_only_is_a_declared_status() -> None:
    """AP-4: a status the history row, the event and the UI have never heard of
    renders as a raw key or drops out of the badge entirely."""
    assert "local_only" in POLISH_STATUSES
    assert len(set(POLISH_STATUSES)) == len(POLISH_STATUSES)


async def test_nothing_local_answering_is_reported_as_local_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user whose local model is not running has a setup fact to act on —
    start it, or pin a cloud family deliberately. Reporting a generic provider
    error would send them hunting for an API key they chose not to use."""
    client = _FakeClient(raises=PolishProviderError("connection refused"))
    _wire_chain(monkeypatch, (ON_DEVICE,), client)

    outcome = await polish_transcript(RAW, language="en", cfg=_Cfg())

    assert outcome.status == "local_only"
    assert outcome.text == RAW
    assert outcome.provider == ON_DEVICE.id
    # The status says why nothing ELSE was tried; the reason says what failed.
    assert outcome.reason == "provider_error"


async def test_a_local_model_that_answers_is_applied_like_any_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``local_only`` is a failure report, not a label for the local family. A
    local model that works produces an ordinary polished transcript."""
    _wire_chain(monkeypatch, (ON_DEVICE,), _FakeClient(reply=POLISHED))

    outcome = await polish_transcript(RAW, language="en", cfg=_Cfg())

    assert outcome.status == "applied"
    assert outcome.text == POLISHED
    assert outcome.provider == ON_DEVICE.id


async def test_a_cloud_chain_that_fails_is_still_a_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new status must not swallow the old one: a cloud outage is a cloud
    outage and should read like one."""
    _wire_chain(monkeypatch, (GROQ,), _FakeClient(raises=PolishProviderError("500")))

    outcome = await polish_transcript(RAW, language="en", cfg=_Cfg())

    assert outcome.status == "provider_error"
    assert outcome.text == RAW


# --------------------------------------------------------------------------- #
# Where the work happens
# --------------------------------------------------------------------------- #


def _use_the_real_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the genuine chain resolver back on the consumer.

    ``tests/unit/conftest.py`` empties it for every unit test, so that a suite
    running on a machine with real keys does not make live model calls. The
    tests below are about the sweep ITSELF — where it runs and how often — so
    they need the real one, and a patch applied in the test body wins over the
    fixture that ran before it.
    """
    monkeypatch.setattr(
        polish, "resolve_polish_chain", polish_client.resolve_polish_chain
    )


def _pin_config_identity(monkeypatch: pytest.MonkeyPatch, path: Any) -> None:
    """Point the chain fingerprint at a file only this test can touch.

    The real one is the live ``jarvis.toml`` in a working tree several sessions
    edit at once; a write by any of them mid-test would invalidate the cache and
    make a "swept once" assertion flap for a reason that has nothing to do with
    the code under test.
    """
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(jarvis_config, "resolve_config_path", lambda: path)


async def test_the_credential_sweep_never_runs_on_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """``get_secret`` reads a file store, calls the OS keyring and may scan
    ``.env``. On a Linux desktop with a locked keyring or a slow Secret Service
    that blocks for seconds — and on the event loop it would take the microphone
    drain task, the WebSocket and the Jarvis Bar down with it, which is exactly
    how the ``load_config`` desktop freeze happened."""
    _use_the_real_resolver(monkeypatch)
    _pin_config_identity(monkeypatch, tmp_path / "jarvis.toml")
    _with_recognizer(monkeypatch, on_device=False)
    loop_thread = threading.get_ident()
    sweep_threads: list[int] = []

    def _slow_get_secret(key: str, env_fallback: str | None = None) -> str | None:
        sweep_threads.append(threading.get_ident())
        return "gsk-test" if key == "groq_api_key" else None

    monkeypatch.setattr(jarvis_config, "get_secret", _slow_get_secret)
    client = _FakeClient(reply=POLISHED)
    monkeypatch.setattr(
        polish, "build_polish_client", lambda family, *, model: client
    )

    outcome = await polish_transcript(RAW, language="en", cfg=_Cfg())

    assert outcome.status == "applied"
    assert sweep_threads, "the real credential sweep never ran"
    assert loop_thread not in sweep_threads, (
        "the credential sweep ran on the event loop; a locked keyring would "
        "stall the microphone, the WebSocket and the bar with it"
    )
    # The provider call itself is async and belongs ON the loop — moving it off
    # would be the opposite mistake.
    assert client.threads == [loop_thread]


async def test_the_chain_is_swept_once_per_settings_change_not_per_dictation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Dictation happens dozens of times an hour and nothing the sweep reads can
    change between two of them unless the user changed a setting. Paying for it
    every time is latency the user feels after the microphone has closed."""
    _pin_config_identity(monkeypatch, tmp_path / "jarvis.toml")
    _with_recognizer(monkeypatch, on_device=False)
    _with_keys(monkeypatch, {"groq_api_key": "gsk-test"})
    sweeps: list[str] = []

    real_resolve = polish_client.resolve_polish_chain

    def _counting_resolve(cfg: Any) -> tuple[PolishFamily, ...]:
        sweeps.append(str(getattr(cfg, "polish_provider", "")))
        return real_resolve(cfg)

    monkeypatch.setattr(polish, "resolve_polish_chain", _counting_resolve)
    monkeypatch.setattr(
        polish, "build_polish_client", lambda family, *, model: _FakeClient(reply=POLISHED)
    )

    for _ in range(3):
        assert (await polish_transcript(RAW, language="en", cfg=_Cfg())).status == (
            "applied"
        )
    assert len(sweeps) == 1, sweeps

    # A changed pin is a changed answer, so the cache must not hold it back.
    await polish_transcript(RAW, language="en", cfg=_Cfg(polish_provider="groq"))
    assert len(sweeps) == 2, sweeps


async def test_a_repaired_credential_is_visible_without_a_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The cache is an optimisation, never a lock-in: the settings screen calls
    ``reset_polish_state`` and the very next dictation must see the new key."""
    _use_the_real_resolver(monkeypatch)
    _pin_config_identity(monkeypatch, tmp_path / "jarvis.toml")
    _with_recognizer(monkeypatch, on_device=False)
    _with_keys(monkeypatch, {})
    monkeypatch.setattr(
        polish, "build_polish_client", lambda family, *, model: _FakeClient(reply=POLISHED)
    )

    first = await polish_transcript(RAW, language="en", cfg=_Cfg())
    assert first.status == "unavailable"
    assert first.reason == "no_credential"

    _with_keys(monkeypatch, {"groq_api_key": "gsk-test"})
    polish.reset_polish_state()

    second = await polish_transcript(RAW, language="en", cfg=_Cfg())
    assert second.status == "applied"


async def test_the_sweep_stays_inside_the_latency_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A sweep in front of the ``wait_for`` is time nothing bounds. A locked
    keyring would then hold the transcript back for as long as it liked, and the
    1200 ms the user was promised would mean nothing."""
    _use_the_real_resolver(monkeypatch)
    _pin_config_identity(monkeypatch, tmp_path / "jarvis.toml")
    # Released in the ``finally`` — a worker thread cannot be cancelled, and
    # leaving one parked would hold up the loop's own shutdown at teardown.
    release = threading.Event()

    def _hangs() -> bool:
        release.wait(10)
        return False  # pragma: no cover — the ceiling fires long before this

    monkeypatch.setattr(polish_client, "stt_runs_on_device", _hangs)
    _with_keys(monkeypatch, {"groq_api_key": "gsk-test"})

    try:
        outcome = await asyncio.wait_for(
            polish_transcript(RAW, language="en", cfg=_Cfg(), timeout_s=0.2),
            timeout=10,
        )
    finally:
        release.set()

    assert outcome.status == "timeout"
    assert outcome.text == RAW


# --------------------------------------------------------------------------- #
# What it costs when the local endpoint is not there
# --------------------------------------------------------------------------- #


@dataclass
class _CountingFactory:
    """A ``build_polish_client`` stand-in that records who was dialled."""

    clients: dict[str, Any]
    dialled: list[str] = field(default_factory=list)

    def __call__(self, family: PolishFamily, *, model: str) -> Any:
        self.dialled.append(family.id)
        return self.clients.get(family.id)


_REFUSED = PolishProviderError("connection refused")


async def test_a_local_endpoint_that_is_not_there_is_not_dialled_every_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinning the local family in front of a cloud one is a supported setup,
    and it is the case where a refusal per dictation never stops: the pass
    SUCCEEDS on the cloud family, so the breaker never opens and nothing else
    would ever notice. One refusal buys a minute of silence instead."""
    factory = _CountingFactory(
        clients={ON_DEVICE.id: _FakeClient(raises=_REFUSED), GROQ.id: _FakeClient(reply=POLISHED)}
    )
    monkeypatch.setattr(polish, "resolve_polish_chain", lambda cfg: (ON_DEVICE, GROQ))
    monkeypatch.setattr(polish, "build_polish_client", factory)

    first = await polish_transcript(RAW, language="en", cfg=_Cfg())
    assert first.status == "applied"
    assert factory.dialled == [ON_DEVICE.id, GROQ.id]

    second = await polish_transcript(RAW, language="en", cfg=_Cfg())
    assert second.status == "applied"
    assert factory.dialled == [ON_DEVICE.id, GROQ.id, GROQ.id]


async def test_an_on_device_install_still_reports_local_only_while_it_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skipping must not change what the user is told. The answer stays
    ``local_only`` — "the recognizer runs here and no local model answered" —
    and the reason names the cause, because that is the setup fact they can
    act on."""
    factory = _CountingFactory(clients={ON_DEVICE.id: _FakeClient(raises=_REFUSED)})
    monkeypatch.setattr(polish, "resolve_polish_chain", lambda cfg: (ON_DEVICE,))
    monkeypatch.setattr(polish, "build_polish_client", factory)

    first = await polish_transcript(RAW, language="en", cfg=_Cfg())
    second = await polish_transcript(RAW, language="en", cfg=_Cfg())

    assert first.status == second.status == "local_only"
    assert first.text == second.text == RAW
    assert second.reason == "local_unreachable"
    assert factory.dialled == [ON_DEVICE.id]


async def test_starting_the_local_model_is_picked_up_after_a_settings_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The memo is an optimisation, never a lock-out: whoever reads
    ``local_only``, starts their model and saves a setting must be polished on
    the next dictation, not after a timer they cannot see."""
    dead = _CountingFactory(clients={ON_DEVICE.id: _FakeClient(raises=_REFUSED)})
    monkeypatch.setattr(polish, "resolve_polish_chain", lambda cfg: (ON_DEVICE,))
    monkeypatch.setattr(polish, "build_polish_client", dead)

    assert (await polish_transcript(RAW, language="en", cfg=_Cfg())).status == (
        "local_only"
    )

    polish.reset_polish_state()
    alive = _CountingFactory(clients={ON_DEVICE.id: _FakeClient(reply=POLISHED)})
    monkeypatch.setattr(polish, "build_polish_client", alive)

    outcome = await polish_transcript(RAW, language="en", cfg=_Cfg())

    assert outcome.status == "applied"
    assert alive.dialled == [ON_DEVICE.id]


async def test_a_cloud_family_is_never_written_off_for_a_minute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The memo is scoped to endpoints on this machine. A cloud provider that
    is unreachable may be a thirty-second network blip, and crossing families
    plus the breaker already answer that — skipping it for a minute would take
    a working provider away from the user for no benefit."""
    factory = _CountingFactory(clients={GROQ.id: _FakeClient(raises=_REFUSED)})
    monkeypatch.setattr(polish, "resolve_polish_chain", lambda cfg: (GROQ,))
    monkeypatch.setattr(polish, "build_polish_client", factory)

    for _ in range(2):
        assert (await polish_transcript(RAW, language="en", cfg=_Cfg())).status == (
            "provider_error"
        )
    assert factory.dialled == [GROQ.id, GROQ.id]


async def test_a_local_endpoint_that_answers_and_refuses_is_dialled_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An HTTP status means something IS listening — a model that was never
    pulled, a 500 — and that is a different fact from "nothing is there". It
    costs one round trip to a running server and it may work as soon as the
    user pulls the model, so it must not be memoised."""
    answered = PolishProviderError("model not found", status=404)
    factory = _CountingFactory(clients={ON_DEVICE.id: _FakeClient(raises=answered)})
    monkeypatch.setattr(polish, "resolve_polish_chain", lambda cfg: (ON_DEVICE,))
    monkeypatch.setattr(polish, "build_polish_client", factory)

    for _ in range(2):
        assert (await polish_transcript(RAW, language="en", cfg=_Cfg())).status == (
            "local_only"
        )
    assert factory.dialled == [ON_DEVICE.id, ON_DEVICE.id]


# --------------------------------------------------------------------------- #
# What it costs when the keyring itself is the slow part
# --------------------------------------------------------------------------- #


def test_a_keyring_that_blocks_and_then_raises_is_not_paid_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure this memo exists for is the SLOW one. A locked keyring that
    raises after seconds is asked once to decide the chain and again to build
    the client, and on the host the whole off-the-loop design was written for
    that is the wait paid twice inside a single dictation."""
    calls: list[str] = []

    def _locked(key: str, env_fallback: str | None = None) -> str | None:
        calls.append(key)
        raise RuntimeError("the keyring is locked")

    monkeypatch.setattr(jarvis_config, "get_secret", _locked)

    assert polish_client.family_has_key(GROQ) is False
    assert polish_client.family_has_key(GROQ) is False

    assert calls == ["groq_api_key"]


def test_a_repaired_keyring_is_still_picked_up_without_a_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The property the memo may not break. An in-app save moves the slot's
    revision, and the entry is keyed on it — so unlocking or replacing a
    credential through the app takes effect immediately, and a repair made
    outside it waits at most the memo's few seconds."""
    monkeypatch.setattr(polish_client, "_SECRET_FAILURE_TTL_S", 0.0)
    calls: list[str] = []

    def _locked(key: str, env_fallback: str | None = None) -> str | None:
        calls.append(key)
        raise RuntimeError("the keyring is locked")

    monkeypatch.setattr(jarvis_config, "get_secret", _locked)
    assert polish_client.family_has_key(GROQ) is False

    _with_keys(monkeypatch, {"groq_api_key": "gsk-test"})
    assert polish_client.family_has_key(GROQ) is True
    assert calls == ["groq_api_key"]


def test_an_in_app_credential_write_invalidates_a_failed_lookup_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No waiting at all for the path the user actually takes: saving a key in
    the API-Keys view bumps ``secret_revision``, which is what the entry is
    tagged with."""
    revision = 0

    def _locked(key: str, env_fallback: str | None = None) -> str | None:
        raise RuntimeError("the keyring is locked")

    monkeypatch.setattr(jarvis_config, "secret_revision", lambda slot: revision)
    monkeypatch.setattr(jarvis_config, "get_secret", _locked)
    assert polish_client.family_has_key(GROQ) is False

    revision = 1
    _with_keys(monkeypatch, {"groq_api_key": "gsk-test"})

    assert polish_client.family_has_key(GROQ) is True


# --------------------------------------------------------------------------- #
# The state is only worth dropping if something drops it
# --------------------------------------------------------------------------- #


async def test_saving_the_dictation_settings_is_what_clears_the_learned_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """``reset_polish_state`` describes itself as the thing a settings change
    calls. Without a caller that sentence is a promise the app does not keep:
    the chain is cached against a fingerprint that a repaired key does not
    move, so the user would go on being told there is no provider until they
    restarted. This is that caller."""
    from jarvis.core.config import DictationConfig
    from jarvis.ui.web.dictation_routes import SettingsBody, put_settings

    _use_the_real_resolver(monkeypatch)
    _pin_config_identity(monkeypatch, tmp_path / "jarvis.toml")
    _with_recognizer(monkeypatch, on_device=False)
    _with_keys(monkeypatch, {})
    monkeypatch.setattr(
        polish, "build_polish_client", lambda family, *, model: _FakeClient(reply=POLISHED)
    )

    before = await polish_transcript(RAW, language="en", cfg=_Cfg())
    assert before.status == "unavailable"
    assert before.reason == "no_credential"

    _with_keys(monkeypatch, {"groq_api_key": "gsk-test"})
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(dictation=DictationConfig())
            )
        )
    )
    saved = await put_settings(SettingsBody(polish=True, persist=False), request)
    assert saved["ok"] is True

    after = await polish_transcript(RAW, language="en", cfg=_Cfg())

    assert after.status == "applied"
