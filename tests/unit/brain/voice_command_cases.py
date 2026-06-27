"""The growing voice-command recognition checklist — real utterances the gate
MUST classify correctly. Add a case here whenever a phrasing is mis-recognised
in the field; the checklist test then guards it forever. (utterance, kind, target);
target is "" for kinds with no target (cancel, depth_deep, depth_fast).
"""
from __future__ import annotations

# (utterance, expected kind, expected target)
RECOGNITION_CASES: list[tuple[str, str, str]] = [
    # provider_switch
    ("wechsel auf gemini", "provider_switch", "gemini"),
    ("switch to openai", "provider_switch", "openai"),
    ("wechsel von gemini auf openai", "provider_switch", "openai"),
    ("switch from claude to gemini", "provider_switch", "gemini"),
    ("nutze chatgpt", "provider_switch", "chatgpt"),
    ("switch to anthropic", "provider_switch", "anthropic"),
    ("ändere den Provider auf gemini", "provider_switch", "gemini"),  # i18n-allow: fixture
    ("stell den Provider auf claude", "provider_switch", "claude"),   # i18n-allow: fixture
    # subagent_switch
    ("stell den subagent provider auf gemini", "subagent_switch", "gemini"),
    ("stell den subagent provider von antigravity auf codex um", "subagent_switch", "codex"),  # i18n-allow: fixture
    # language_switch
    ("stell auf Englisch um", "language_switch", "en"),               # i18n-allow: fixture
    ("antworte auf deutsch und englisch", "language_switch", "de"),   # i18n-allow: fixture
    ("respond in German", "language_switch", "de"),
    # cancel
    ("jarvis stopp", "cancel", ""),
    ("halt", "cancel", ""),
    # depth
    ("denk gründlich", "depth_deep", ""),                             # i18n-allow: fixture
    ("nimm haiku", "depth_fast", ""),
]
