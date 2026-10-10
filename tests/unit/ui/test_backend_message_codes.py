"""Parity: every message code the backend sends has a translation in the UI.

Several routes and events return a stable ``*_code`` next to an English
sentence; the desktop UI renders ``<namespace>.<code>`` from its locale files
and falls back to the English sentence for a code it does not know. A code
without a translation therefore degrades silently to English, which is exactly
the drift this test catches (AP-4: Python code tables <-> locale JSON).

Where the English sentence is static, the ``en`` translation must also match
the backend sentence word for word, so the two cannot drift apart.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jarvis.board.achievements import ACHIEVEMENTS
from jarvis.codex_auth import CODEX_STATUS_MESSAGE_CODES
from jarvis.google_cli.auth_service import GOOGLE_CLI_STATUS_MESSAGE_CODES
from jarvis.marketplace.amd_mcp import AMD_UNAVAILABLE_REASONS, amd_unavailable_reason_code
from jarvis.mcp.state import CLAUDE_DESKTOP_IMPORT_NOTE_CODES
from jarvis.speech.wake_phrase import WAKE_PLAN_MESSAGE_CODES, resolve_wake_plan
from jarvis.trigger.hotkey import MOUSE_HOTKEY_REASONS, mouse_hotkeys_reason_code
from jarvis.ui.web.settings_routes import WAKE_HINT_CODES, WAKE_MESSAGE_CODES

_REPO = Path(__file__).resolve().parents[3]
_LOCALES = _REPO / "jarvis" / "ui" / "web" / "frontend" / "src" / "i18n" / "locales"
_LANGS = ("en", "de", "es", "zh", "pt")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

#: Codes the realtime/live sessions attach to sentences they author themselves.
_VOICE_BACKEND_ERROR_CODES = ("voice_connection_lost", "subscription_request_failed")


def _locale(lang: str) -> dict:
    return json.loads((_LOCALES / f"{lang}.json").read_text(encoding="utf-8"))


def _lookup(tree: dict, dotted: str) -> object:
    node: object = tree
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _fill(template: str, params: dict[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: params.get(m.group(1), m.group(0)), template)


_TABLES: list[tuple[str, tuple[str, ...]]] = [
    ("settings_view.wake_word.msg", WAKE_PLAN_MESSAGE_CODES + WAKE_MESSAGE_CODES),
    ("settings_view.wake_word.hint", WAKE_HINT_CODES),
    ("settings_view.keybinds.mouse_reason", tuple(MOUSE_HOTKEY_REASONS)),
    ("apikeys_codex.status_msg", CODEX_STATUS_MESSAGE_CODES),
    ("apikeys_antigravity.status_msg", GOOGLE_CLI_STATUS_MESSAGE_CODES),
    ("mcps_view.import_note", CLAUDE_DESKTOP_IMPORT_NOTE_CODES),
    ("plugins_view.unavailable_reason", tuple(AMD_UNAVAILABLE_REASONS)),
    ("voice.backend_error", _VOICE_BACKEND_ERROR_CODES),
    ("use_web_socket.achievement_title", tuple(a.id for a in ACHIEVEMENTS)),
]


@pytest.mark.parametrize("lang", _LANGS)
@pytest.mark.parametrize(("namespace", "codes"), _TABLES, ids=[t[0] for t in _TABLES])
def test_every_backend_code_has_a_translation(
    lang: str, namespace: str, codes: tuple[str, ...]
) -> None:
    tree = _locale(lang)
    en = _locale("en")
    missing = [c for c in codes if not isinstance(_lookup(tree, f"{namespace}.{c}"), str)]
    assert not missing, f"{lang}: no translation for {namespace}.{missing}"
    for code in codes:
        value = _lookup(tree, f"{namespace}.{code}")
        source = _lookup(en, f"{namespace}.{code}")
        assert sorted(_PLACEHOLDER.findall(value)) == sorted(_PLACEHOLDER.findall(source)), (
            f"{lang} {namespace}.{code}: placeholders differ from en"
        )
        # `{name}` is the assistant-name token in the UI; a backend value must
        # never ride on it.
        assert "{name}" not in value


def test_static_english_sentences_match_the_backend() -> None:
    en = _locale("en")
    for code, sentence in MOUSE_HOTKEY_REASONS.items():
        assert _lookup(en, f"settings_view.keybinds.mouse_reason.{code}") == sentence
        assert mouse_hotkeys_reason_code(sentence) == code
    for code, sentence in AMD_UNAVAILABLE_REASONS.items():
        assert _lookup(en, f"plugins_view.unavailable_reason.{code}") == sentence
        assert amd_unavailable_reason_code(sentence) == code
    for spec in ACHIEVEMENTS:
        assert _lookup(en, f"use_web_socket.achievement_title.{spec.id}") == spec.title
    assert mouse_hotkeys_reason_code("some other sentence") == ""
    assert amd_unavailable_reason_code(None) == ""


def test_seed_catalog_longevity_notes_are_translated_verbatim() -> None:
    """A shipped plugin's longevity note is translated by plugin id; the ``en``
    entry must equal the catalog's own note, so an edited note cannot keep
    showing a stale translation unnoticed."""
    raw = json.loads(
        (_REPO / "jarvis" / "marketplace" / "seed_catalog.json").read_text(encoding="utf-8")
    )
    plugins = raw.get("plugins", raw) if isinstance(raw, dict) else raw
    notes = {}
    for plugin in plugins:
        note = plugin.get("longevity_note") or (
            (plugin.get("extensions") or {}).get("io.github.personaljarvis", {}).get(
                "longevity_note"
            )
        )
        if note:
            notes[plugin["id"]] = note
    assert notes
    for lang in _LANGS:
        table = _lookup(_locale(lang), "plugins_view.longevity_note")
        assert isinstance(table, dict)
        assert set(table) == set(notes), lang
    en = _lookup(_locale("en"), "plugins_view.longevity_note")
    assert en == notes


class _WakeCfg:
    def __init__(self, phrase: str) -> None:
        self.phrase = phrase
        self.engine = "auto"
        self.custom_model_path = ""
        self.fuzzy_match_ratio = 0.8


@pytest.mark.parametrize(
    ("phrase", "whisper", "language", "code"),
    [
        ("Hey Nova", False, "de", "needs_local_model"),
        ("", False, "de", "needs_local_model_no_phrase"),
        ("Hey Nova", True, "de", "stt_match_unreliable"),
        ("Hey Nova", True, None, "stt_match_unreliable_any_language"),
    ],
)
def test_wake_plan_codes_render_the_backend_sentence(
    phrase: str, whisper: bool, language: str | None, code: str
) -> None:
    """The ``en`` template filled with the plan's params reads exactly like the
    plan's own English message."""
    plan = resolve_wake_plan(
        _WakeCfg(phrase),
        local_whisper_available=whisper,
        language=language,
        vosk_available=False,
    )
    if not phrase and plan.message_code != code:
        pytest.skip("an empty phrase resolves to the default wake phrase here")
    assert plan.message_code == code
    template = _lookup(_locale("en"), f"settings_view.wake_word.msg.{code}")
    assert _fill(template, dict(plan.message_params)) == plan.message
