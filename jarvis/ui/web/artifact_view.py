"""Render a text/markdown artifact into a standalone, styled HTML page.

Used by GET /api/outputs/{slug}/files/{path}/view so the user can "open in
browser" a markdown deliverable and see it rendered (headings, tables, lists)
instead of raw '#'-prefixed text. The page is self-contained (inline CSS) and is
served with a strict no-script CSP (see VIEW_CSP in outputs_routes) so a
malicious/hallucinated artifact can never execute JS in the app origin.

Degrades gracefully: if the optional `markdown` library is unavailable, the raw
text is shown escaped inside <pre> so the base install never hard-fails.
"""

from __future__ import annotations

import html
import logging

from jarvis.artifacts.design_guide import THEME_CSS

log = logging.getLogger(__name__)

_MARKDOWN_EXT = (".md", ".markdown")

# No-script CSP for the /view page (referenced by outputs_routes). Neutralizes
# XSS from artifact content rendered in the app origin.
VIEW_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:;"

# The artifact-page CSP (``/files/{path}/page`` in outputs_routes): an artifact
# is a self-contained page a worker wrote to be LOOKED AT and used — tabs,
# filters, a chart drawn on canvas — so inline scripts run. What stays shut is
# every way out: no network of any kind (no fetch, no remote script, font,
# image or frame), no forms posting anywhere, no navigation. The frontend
# frames such a page with ``sandbox="allow-scripts"`` and WITHOUT
# ``allow-same-origin``, so the script runs in an opaque origin that cannot
# reach the app's cookies, storage or API — the same model Claude artifacts
# use. ``/view`` and inline downloads keep VIEW_CSP: those are arbitrary
# worker files, not artifacts.
ARTIFACT_PAGE_CSP = (
    "default-src 'none'; "
    "script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; "
    "img-src data: blob:; "
    "font-src data:; "
    "media-src data: blob:; "
    "connect-src 'none'; "
    "form-action 'none'; "
    "frame-src 'none'; "
    "base-uri 'none';"
)

# The artifact design standard (jarvis/artifacts/design_guide.py): the app's
# own tokens in both palettes, so a Markdown output opened in the browser
# looks like the page it sits next to in the Artifacts section — and follows
# the app's theme through the same `?theme=light|dark` query the artifact
# stage appends, stamped on <html> by the SERVER (the page stays script-free
# under VIEW_CSP; the guide's bootstrap script is for worker-written pages).
# The tokens are the guide's verbatim; only the document rules below are this
# page's own.
_PAGE_CSS = (
    THEME_CSS + "\n"
    "body{padding:40px 24px 64px}"
    "main{max-width:72ch;margin:0 auto}"
    "h1,h2,h3,h4{margin:1.6em 0 .5em}"
    "main>h1:first-of-type{margin-top:0}"
    "h4{font-size:14px;font-weight:600}"
    "p,ul,ol{margin:.7em 0;max-width:none}li{margin:.25em 0}"
    "pre{background:var(--bg-2);border:1px solid var(--line);padding:14px 16px;"
    "overflow:auto;border-radius:var(--radius-sm);font-size:13px;line-height:1.5}"
    "code{background:var(--bg-3);border:1px solid var(--line);padding:.1em .35em;"
    "border-radius:4px}"
    "pre code{background:none;border:none;padding:0;font-size:inherit}"
    "table{border-collapse:collapse;margin:1em 0;display:block;overflow-x:auto;"
    "font-size:14px}"
    "th,td{border:1px solid var(--line);padding:.45rem .7rem;text-align:left}"
    "th{background:var(--bg-2);font-weight:600}"
    "blockquote{border-left:2px solid var(--accent);margin:1em 0;"
    "padding:.1em 0 .1em 1rem;color:var(--ink-2)}"
    "img{max-width:100%;border-radius:var(--radius-sm)}"
    "hr{border:none;border-top:1px solid var(--line);margin:2em 0}"
    ".artifact-name{margin:0 0 24px;padding-bottom:12px;border-bottom:1px solid var(--line)}"
)


_THEMES = frozenset({"light", "dark"})


def _shell(title: str, body_html: str, theme: str | None) -> str:
    stamp = f" data-theme='{theme}'" if theme in _THEMES else ""
    return (
        f"<!doctype html><html lang='en'{stamp}><head><meta charset='utf-8'>"
        f'<meta http-equiv="Content-Security-Policy" content="{VIEW_CSP}">'
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_PAGE_CSS}</style></head>"
        f"<body><main><p class='artifact-name eyebrow'>{html.escape(title)}</p>"
        f"{body_html}</main></body></html>"
    )


def render_artifact_html(filename: str, text: str, *, theme: str | None = None) -> str:
    """Return a complete HTML document rendering *text*.

    Markdown filenames are rendered to HTML via the `markdown` library; everything
    else (and the no-markdown-lib fallback) is shown escaped in <pre>. *theme*
    (``light`` / ``dark``, anything else ignored) pins the palette the way the
    app's Artifacts stage pins it; without it the page follows the OS. Never raises.
    """
    if filename.lower().endswith(_MARKDOWN_EXT):
        try:
            import markdown  # lazy: optional dep; base install may lack it

            body = markdown.markdown(
                text,
                extensions=["extra", "sane_lists", "tables", "fenced_code"],
            )
            return _shell(filename, body, theme)
        except Exception as exc:  # noqa: BLE001 — a view must never 500
            log.info("markdown render unavailable (%s) — serving raw <pre>", exc)
    return _shell(filename, f"<pre>{html.escape(text)}</pre>", theme)


__all__ = ["ARTIFACT_PAGE_CSP", "VIEW_CSP", "render_artifact_html"]
