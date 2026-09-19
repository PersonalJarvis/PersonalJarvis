"""Spoken/written copilot replies (ja + en; other locales fall back to en)."""

from __future__ import annotations

from jarvis.core.turn_language import localized

_R: dict[str, dict[str, str]] = {
    "material_done": {
        "en": (
            "Deck ready: {slides} slides, quality check {passed}/{total} passed"
            ". Files: {folder}. Nothing was sent; review them before you delive"
            "r."
        ),
        "ja": (
            "\u8cc7\u6599\u304c\u3067\u304d\u307e\u3057\u305f\u3002\u5168{slide"
            "s}\u679a\u3001\u54c1\u8cea\u30c1\u30a7\u30c3\u30af\u306f{total}"
            "\u9805\u76ee\u4e2d{passed}\u9805\u76ee\u5408\u683c\u3067\u3059"
            "\u3002\u4fdd\u5b58\u5148\uff1a{folder}\u3002\u9001\u4fe1\u306f"
            "\u3057\u3066\u3044\u306a\u3044\u306e\u3067\u3001\u78ba\u8a8d\u3057"
            "\u3066\u304b\u3089\u7d0d\u54c1\u3057\u3066\u304f\u3060\u3055\u3044"
            "\u3002"
        ),
    },
    "material_failed": {
        "en": "The deck could not be made: {error}",
        "ja": (
            "\u8cc7\u6599\u3092\u4f5c\u308c\u307e\u305b\u3093\u3067\u3057\u305f"
            "\uff1a{error}"
        ),
    },
    "wf_started": {
        "en": (
            "Observing your work: which app is in front, every few seconds. No "
            "keystrokes, no screenshots. Say \"stop observing my work\" when do"
            "ne."
        ),
        "ja": (
            "\u4f5c\u696d\u306e\u89b3\u5bdf\u3092\u59cb\u3081\u307e\u3057\u305f"
            "\u3002\u6570\u79d2\u3054\u3068\u306b\u524d\u9762\u306e\u30a2\u30d7"
            "\u30ea\u3060\u3051\u3092\u8a18\u9332\u3057\u307e\u3059\u3002\u30ad"
            "\u30fc\u5165\u529b\u3084\u753b\u9762\u306f\u8a18\u9332\u3057\u307e"
            "\u305b\u3093\u3002\u7d42\u308f\u3063\u305f\u3089\u300c\u4f5c\u696d"
            "\u306e\u89b3\u5bdf\u3092\u7d42\u4e86\u300d\u3068\u8a00\u3063\u3066"
            "\u304f\u3060\u3055\u3044\u3002"
        ),
    },
    "wf_already": {
        "en": "I am already observing your work.",
        "ja": (
            "\u3059\u3067\u306b\u4f5c\u696d\u3092\u89b3\u5bdf\u3057\u3066\u3044"
            "\u307e\u3059\u3002"
        ),
    },
    "wf_not_running": {
        "en": "I am not observing anything right now.",
        "ja": (
            "\u4eca\u306f\u4f5c\u696d\u3092\u89b3\u5bdf\u3057\u3066\u3044\u307e"
            "\u305b\u3093\u3002"
        ),
    },
    "wf_unavailable": {
        "en": (
            "Work observation needs Windows; it is not available on this system"
            "."
        ),
        "ja": (
            "\u4f5c\u696d\u306e\u89b3\u5bdf\u306fWindows\u3067\u306e\u307f"
            "\u4f7f\u3048\u307e\u3059\u3002\u3053\u306e\u30b7\u30b9\u30c6\u30e0"
            "\u3067\u306f\u4f7f\u3048\u307e\u305b\u3093\u3002"
        ),
    },
    "wf_done": {
        "en": "Observation stopped. Report saved to {path}.\n\n{report}",
        "ja": (
            "\u89b3\u5bdf\u3092\u7d42\u4e86\u3057\u307e\u3057\u305f\u3002\u30ec"
            "\u30dd\u30fc\u30c8\u3092{path}\u306b\u4fdd\u5b58\u3057\u307e\u3057"
            "\u305f\u3002\n\n{report}"
        ),
    },
    "material_none": {
        "en": "There is no deck yet. Ask me to make one first.",
        "ja": (
            "\u307e\u3060\u8cc7\u6599\u304c\u3042\u308a\u307e\u305b\u3093\u3002"
            "\u5148\u306b\u8cc7\u6599\u3092\u4f5c\u308b\u3088\u3046\u983c\u3093"
            "\u3067\u304f\u3060\u3055\u3044\u3002"
        ),
    },
    "material_no_page": {
        "en": "The deck has {pages} pages; there is no page {page}.",
        "ja": (
            "\u8cc7\u6599\u306f\u5168{pages}\u30da\u30fc\u30b8\u3067\u3059"
            "\u3002{page}\u30da\u30fc\u30b8\u76ee\u306f\u3042\u308a\u307e\u305b"
            "\u3093\u3002"
        ),
    },
    "material_revised": {
        "en": (
            "Page {page} revised. Quality check {passed}/{total} passed. Files:"
            " {folder}. Nothing was sent."
        ),
        "ja": (
            "{page}\u30da\u30fc\u30b8\u76ee\u3092\u76f4\u3057\u307e\u3057\u305f"
            "\u3002\u54c1\u8cea\u30c1\u30a7\u30c3\u30af\u306f{total}\u9805"
            "\u76ee\u4e2d{passed}\u9805\u76ee\u5408\u683c\u3067\u3059\u3002"
            "\u4fdd\u5b58\u5148\uff1a{folder}\u3002\u9001\u4fe1\u306f\u3057"
            "\u3066\u3044\u307e\u305b\u3093\u3002"
        ),
    },
    "wf_no_candidate": {
        "en": "There is no candidate {n} (the last observation found {count}).",
        "ja": (
            "\u5019\u88dc{n}\u306f\u3042\u308a\u307e\u305b\u3093\uff08\u524d"
            "\u56de\u306e\u89b3\u5bdf\u3067\u898b\u3064\u304b\u3063\u305f\u5019"
            "\u88dc\u306f{count}\u4ef6\u3067\u3059\uff09\u3002"
        ),
    },
    "wf_drafted": {
        "en": (
            "Draft script for candidate {n} saved to {path}. It was NOT run: re"
            "ad it, fill the TODOs and run it yourself."
        ),
        "ja": (
            "\u5019\u88dc{n}\u306e\u30b9\u30af\u30ea\u30d7\u30c8\u306e\u4e0b"
            "\u66f8\u304d\u3092{path}\u306b\u4fdd\u5b58\u3057\u307e\u3057\u305f"
            "\u3002\u5b9f\u884c\u306f\u3057\u3066\u3044\u307e\u305b\u3093\u3002"
            "\u4e2d\u8eab\u3092\u78ba\u8a8d\u3057\u3001TODO\u3092\u57cb\u3081"
            "\u3066\u304b\u3089\u3054\u81ea\u8eab\u3067\u5b9f\u884c\u3057\u3066"
            "\u304f\u3060\u3055\u3044\u3002"
        ),
    },
}


def reply(key: str, lang: str, **fmt: object) -> str:
    return localized(_R[key], lang).format(**fmt)


__all__ = ["reply"]
