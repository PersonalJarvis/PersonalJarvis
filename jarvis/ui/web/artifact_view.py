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

log = logging.getLogger(__name__)

_MARKDOWN_EXT = (".md", ".markdown")

# No-script CSP for the /view page (referenced by outputs_routes). Neutralizes
# XSS from artifact content rendered in the app origin.
VIEW_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:;"

# Brand theme (matte black + signal yellow), kept in lockstep with the desktop
# app's dark tokens (frontend/src/index.css) and video/src/intro/theme.ts. The
# app is dark-first and this page opens from inside it, so it commits to the
# brand look instead of following the OS light/dark preference.
_PAGE_CSS = (
    "*{box-sizing:border-box}"
    "body{max-width:52rem;margin:0 auto;padding:2.5rem 1.5rem 4rem;"
    "font:15px/1.65 -apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;"
    "color:#FAFAFA;background:#0A0A0A}"
    "h1,h2,h3,h4{line-height:1.3;margin:1.6em 0 .6em;font-weight:600}"
    "h1:first-child,h2:first-child{margin-top:0}"
    "h1{font-size:1.5rem;padding-bottom:.4em;border-bottom:1px solid rgba(255,255,255,0.10)}"
    "h2{font-size:1.2rem}h3{font-size:1.05rem}"
    "a{color:#FFD60A}a:hover{color:#E6BE00}"
    "p,ul,ol{margin:.7em 0}li{margin:.25em 0}"
    "pre{background:#141414;border:1px solid rgba(255,255,255,0.10);"
    "padding:1rem;overflow:auto;border-radius:10px}"
    "code{background:#181818;border:1px solid rgba(255,255,255,0.10);"
    "padding:.1em .35em;border-radius:5px;font-size:.92em}"
    "pre code{background:none;border:none;padding:0}"
    "table{border-collapse:collapse;margin:1em 0;display:block;overflow-x:auto}"
    "th,td{border:1px solid rgba(255,255,255,0.14);padding:.45rem .7rem}"
    "th{background:#141414;text-align:left}"
    "blockquote{border-left:3px solid #FFD60A;margin:1em 0;"
    "padding:.1em 0 .1em 1rem;color:#9A9A9A}"
    "img{max-width:100%;border-radius:8px}"
    "hr{border:none;border-top:1px solid rgba(255,255,255,0.10);margin:2em 0}"
    ".artifact-name{font-size:11px;letter-spacing:.14em;text-transform:uppercase;"
    "color:#9A9A9A;margin:0 0 1.6rem;display:flex;align-items:center;gap:.6em}"
    ".artifact-name::before{content:'';width:9px;height:9px;border-radius:50%;"
    "background:#FFD60A;box-shadow:0 0 10px rgba(255,214,10,0.35)}"
)


def _shell(title: str, body_html: str) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f'<meta http-equiv="Content-Security-Policy" content="{VIEW_CSP}">'
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_PAGE_CSS}</style></head>"
        f"<body><p class='artifact-name'>{html.escape(title)}</p>{body_html}</body></html>"
    )


def render_artifact_html(filename: str, text: str) -> str:
    """Return a complete HTML document rendering *text*.

    Markdown filenames are rendered to HTML via the `markdown` library; everything
    else (and the no-markdown-lib fallback) is shown escaped in <pre>. Never raises.
    """
    if filename.lower().endswith(_MARKDOWN_EXT):
        try:
            import markdown  # lazy: optional dep; base install may lack it

            body = markdown.markdown(
                text,
                extensions=["extra", "sane_lists", "tables", "fenced_code"],
            )
            return _shell(filename, body)
        except Exception as exc:  # noqa: BLE001 — a view must never 500
            log.info("markdown render unavailable (%s) — serving raw <pre>", exc)
    return _shell(filename, f"<pre>{html.escape(text)}</pre>")


__all__ = ["VIEW_CSP", "render_artifact_html"]
