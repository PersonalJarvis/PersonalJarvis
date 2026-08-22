"""Is the frontend build on disk whole enough to hand to a browser?

``index.html`` and the hashed bundles it points at are written by separate
steps of one ``npm run build``, and Vite runs with ``emptyOutDir: true`` — so
``dist/`` passes through a state where ``index.html`` is still the OLD one
while ``dist/assets/`` has already been deleted. Measured on 2026-08-22 with a
200 ms poll against the live server during a real rebuild:

* 1.8 s serving the previous ``index.html`` whose ``<script type="module">``
  answered 404,
* then 5.2 s with no ``index.html`` at all (the holding page),
* then the new build.

The middle 5.2 s are harmless: the holding page refreshes itself. The first
1.8 s are not. A window that loads that document gets the boot splash, its
entry bundle 404s, and NOTHING further runs — no React, no bundle watch, no
preload recovery. The only thing left is the blank-window watchdog in
``index.html``, which allows a splash 20 seconds before it acts. Twenty seconds
of a near-black window with a small spinner is what gets reported as "it goes
black when it reloads by itself".

The fix is to never serve that document. An ``index.html`` whose own entry
assets are not on disk is not a page, it is a trap: the caller falls back to
the holding page, which says what is happening and comes back on its own.

Deliberately un-cached. The whole point is that ``index.html`` can stay
byte-identical while the files underneath it disappear, so a result keyed on
its mtime would be exactly wrong. Costs one small read plus a handful of
``stat`` calls, on a route a window hits when it loads and once per bundle-watch
poll.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["referenced_assets", "build_is_complete", "holding_page_html", "HOLDING_MARKER"]

# Present in the holding page and in nothing else, so a page that is waiting
# for a build can tell — from the document alone — whether the server is ready
# to hand it a real one yet. Both the page below and the frontend's automatic
# reloads test for it.
HOLDING_MARKER = "jarvis-build-holding"

# Every hashed build output index.html points at. Mirrors the frontend's own
# `bundleFingerprint` (src/lib/bundleWatch.ts) — keep the two patterns in
# lockstep, they are answering the same question from opposite ends.
_ASSET_REF = re.compile(r"/assets/[A-Za-z0-9._-]+")


def referenced_assets(html: str) -> list[str]:
    """The ``/assets/...`` URLs *html* asks the browser to load.

    Order-preserving and de-duplicated, so a caller can report the first
    missing one without a second pass.
    """
    seen: dict[str, None] = {}
    for ref in _ASSET_REF.findall(html):
        seen.setdefault(ref, None)
    return list(seen)


def build_is_complete(index_file: Path, dist_dir: Path) -> bool:
    """True when *index_file* and every asset it references exist on disk.

    False for a missing/unreadable index, and false while a rebuild has taken
    the assets out from under an index that is still there. A build that
    references no assets at all is treated as complete — that is a hand-written
    or placeholder page, not a half-written Vite output.
    """
    try:
        html = index_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for ref in referenced_assets(html):
        candidate = dist_dir / ref.lstrip("/")
        try:
            if not candidate.is_file():
                return False
        except OSError:
            return False
    return True


def holding_page_html() -> str:
    """The page a window gets while a rebuild is between two states on disk.

    It is the boot splash, deliberately: the same ground, the same ring, the
    same theme resolution from the same ``localStorage`` key as
    ``frontend/index.html`` — so a rebuild looks like the app starting rather
    than like a different program taking over the window. Keep the colours and
    the storage key in lockstep with that file.

    It does NOT use ``<meta http-equiv="refresh">``. A blind timer reloads into
    the same half-written build over and over, which is the flicker this whole
    area keeps producing. Instead it asks the server for the entry document and
    reloads only once the answer is no longer this page — meaning a complete
    build is on disk and a reload will land on something that runs.
    """
    return (
        "<!doctype html>"
        '<html lang="en" class="dark"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        "<title>Assistant</title>"
        "<script>(function(){var m='dark';try{var s=(localStorage.getItem("
        "'jarvis.theme')||'').trim();if(s==='light'||s==='dark'){m=s;}"
        "else if(s==='system'){m=window.matchMedia('(prefers-color-scheme: light)')"
        ".matches?'light':'dark';}}catch(e){}"
        "var r=document.documentElement;r.classList.toggle('dark',m==='dark');"
        "r.style.colorScheme=m;})();</script>"
        "<style>"
        ":root{--jbs-bg:#fcfbf8;--jbs-accent:#a86b00;"
        "--jbs-ring:rgba(168,107,0,0.22);--jbs-name:#2b2b33;--jbs-sub:#6b6b76}"
        ":root.dark{--jbs-bg:#0a0e14;--jbs-accent:#e7c46e;"
        "--jbs-ring:rgba(231,196,110,0.2);--jbs-name:#e6e6e6;--jbs-sub:#9aa3ad}"
        "html,body{margin:0;height:100%;background:var(--jbs-bg)}"
        "#s{position:fixed;inset:0;display:flex;flex-direction:column;"
        "align-items:center;justify-content:center;gap:18px;"
        "background:var(--jbs-bg);color:var(--jbs-accent);"
        "font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}"
        "#s .ring{width:38px;height:38px;border-radius:50%;"
        "border:3px solid var(--jbs-ring);border-top-color:var(--jbs-accent);"
        "animation:sp 0.8s linear infinite}"
        "#s .name{font-size:15px;font-weight:600;letter-spacing:0.04em;"
        "color:var(--jbs-name)}"
        "#s .sub{font-size:12px;color:var(--jbs-sub);letter-spacing:0.06em;"
        "text-transform:uppercase}"
        "@keyframes sp{to{transform:rotate(360deg)}}"
        "</style></head><body>"
        f'<div id="s" data-state="{HOLDING_MARKER}">'
        '<div class="ring"></div>'
        '<div class="name"></div>'
        '<div class="sub">Updating…</div>'
        "</div>"
        "<script>(function(){try{var n=(localStorage.getItem("
        "'jarvis.assistantName')||'').trim();if(n){"
        "document.querySelector('#s .name').textContent=n;document.title=n;}}"
        "catch(e){}})();</script>"
        "<script>(function(){"
        "var tries=0;"
        "function look(){"
        "tries++;"
        "fetch('/',{cache:'no-store',headers:{Accept:'text/html'}})"
        f".then(function(r){{return r.ok?r.text():'{HOLDING_MARKER}';}})"
        f".then(function(t){{if(t.indexOf('{HOLDING_MARKER}')===-1){{"
        "location.reload();return;}"
        "setTimeout(look, tries<20?700:3000);})"
        "['catch'](function(){setTimeout(look, tries<20?700:3000);});"
        "}"
        "setTimeout(look,700);"
        "})();</script>"
        "</body></html>"
    )
