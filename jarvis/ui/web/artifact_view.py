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

_PAGE_CSS = (
    "body{max-width:48rem;margin:2rem auto;padding:0 1rem;"
    "font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a;background:#fff}"
    "pre{background:#f4f4f4;padding:1rem;overflow:auto;border-radius:6px}"
    "code{background:#f4f4f4;padding:.1em .3em;border-radius:3px}"
    "pre code{background:none;padding:0}"
    "table{border-collapse:collapse}th,td{border:1px solid #ddd;padding:.4rem .6rem}"
    "blockquote{border-left:3px solid #ddd;margin:0;padding-left:1rem;color:#555}"
    "img{max-width:100%}"
    "@media(prefers-color-scheme:dark){body{background:#1a1a1a;color:#e8e8e8}"
    "pre,code{background:#2a2a2a}th,td{border-color:#444}}"
)


def _shell(title: str, body_html: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f'<meta http-equiv="Content-Security-Policy" content="{VIEW_CSP}">'
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_PAGE_CSS}</style></head>"
        f"<body>{body_html}</body></html>"
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
