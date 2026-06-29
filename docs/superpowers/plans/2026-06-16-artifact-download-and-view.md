# Artifact Download & View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the user, in the Outputs view, per-artifact actions to download a file to the
Downloads folder and open/view it in the browser (Markdown server-rendered), plus native
"reveal in folder" / "open with default app" actions that appear only on a local desktop run —
cross-platform on Windows, macOS, Linux, and the headless VPS-browser runtime.

**Architecture:** New backend routes in `outputs_routes.py` reuse the existing `_is_deliverable_relpath`
allowlist via a shared `_resolve_artifact_target` resolver, serving files with `FileResponse`
(`Content-Disposition`) and a server-rendered HTML view for Markdown. A new isolated
`jarvis/platform/open_path.py` provides the per-OS native open/reveal with a headless no-op
fallback. A launcher flag (`app.state.native_file_actions`) gates the native actions; the frontend
discovers it via `GET /api/outputs/capabilities`.

**Tech Stack:** FastAPI / Starlette (`FileResponse`, `HTMLResponse`), Python `markdown` (new base dep,
pure-Python), React + React Query (`fetch`), lucide-react icons, pytest, vitest.

**Interpreter note:** Run pytest via `C:\Program Files\Python311\python.exe -m pytest` — the repo's
default `python` may be a venv without pytest. Frontend tests run from
`jarvis/ui/web/frontend/` with `npm run test`.

**Shared-tree note:** The working tree is edited by parallel sessions. Every commit step stages only
the exact files it names (pathspec-scoped) — never `git add -A`.

---

## File Structure

| File | Responsibility | New/Modify |
|---|---|---|
| `jarvis/platform/open_path.py` | Cross-platform `open_file` / `reveal_in_folder` with headless no-op | **New** |
| `jarvis/ui/web/artifact_view.py` | Render a text/markdown artifact into a standalone, CSP-locked HTML page | **New** |
| `jarvis/ui/web/outputs_routes.py` | `_resolve_artifact_target` + `/download`, `/view`, `/capabilities`, `/reveal`, `/open-native` | Modify |
| `jarvis/ui/web/launcher.py` | `app.state.native_file_actions = False` in `_run_headless` | Modify |
| `jarvis/ui/desktop_app.py` | `app.state.native_file_actions = True` in the desktop backend wiring | Modify |
| `jarvis/ui/web/frontend/src/hooks/useOutputs.ts` | `useOutputsCapabilities` + URL/classify/native helpers | Modify |
| `jarvis/ui/web/frontend/src/views/OutputsView.tsx` | Action-button cluster in `ArtifactRow`, capability fetch in `ArtifactsSection` | Modify |
| `pyproject.toml` | Add `markdown>=3.5` to base `dependencies` | Modify |
| `tests/unit/platform/test_open_path.py` | open_path unit tests | **New** |
| `tests/unit/ui/web/test_artifact_view.py` | render helper unit tests | **New** |
| `tests/unit/ui/web/test_outputs_routes.py` | new route tests (append) | Modify |
| `jarvis/ui/web/frontend/src/hooks/useOutputs.classify.test.ts` | classify/URL helper tests | **New** |

---

## Task 1: Cross-platform `open_path.py`

**Files:**
- Create: `jarvis/platform/open_path.py`
- Test: `tests/unit/platform/test_open_path.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/platform/test_open_path.py`:

```python
"""Unit tests for the cross-platform open/reveal helpers (per-OS argv + no-op)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import jarvis.platform.open_path as op
from jarvis.platform.capabilities import Capabilities


def _caps(display: bool = True) -> Capabilities:
    return Capabilities(
        platform="linux",
        has_hotkey=False,
        has_ax_tree=False,
        has_overlay=False,
        has_pty=False,
        has_elevation=False,
        has_cursor=False,
        display_present=display,
        is_wayland=False,
        ax_permission_granted=None,
    )


def test_open_file_linux_uses_xdg_open():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="linux"), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.open_file(Path("/x/y.md")) is True
        argv = popen.call_args.args[0]
        assert argv[0] == "xdg-open" and argv[1] == "/x/y.md"


def test_open_file_darwin_uses_open():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="darwin"), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.open_file(Path("/x/y.md")) is True
        assert popen.call_args.args[0][0] == "open"


def test_open_file_windows_uses_startfile():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="win32"), \
         patch.object(op.os, "startfile", create=True) as startfile:
        assert op.open_file(Path("C:/x/y.md")) is True
        startfile.assert_called_once()


def test_open_file_headless_is_noop():
    with patch.object(op, "detect_capabilities", return_value=_caps(display=False)), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.open_file(Path("/x/y.md")) is False
        popen.assert_not_called()


def test_reveal_linux_opens_parent_dir():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="linux"), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.reveal_in_folder(Path("/x/y/z.md")) is True
        argv = popen.call_args.args[0]
        assert argv[0] == "xdg-open" and argv[1] == "/x/y"


def test_reveal_windows_uses_explorer_select():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="win32"), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.reveal_in_folder(Path(r"C:\x\y\z.md")) is True
        argv = popen.call_args.args[0]
        assert argv[0] == "explorer" and argv[1] == "/select,"


def test_reveal_headless_is_noop():
    with patch.object(op, "detect_capabilities", return_value=_caps(display=False)), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.reveal_in_folder(Path("/x/y/z.md")) is False
        popen.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/platform/test_open_path.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis.platform.open_path'`

- [ ] **Step 3: Write minimal implementation**

Create `jarvis/platform/open_path.py`:

```python
"""Cross-platform "open a file" / "reveal in folder" helpers (AD-5/AD-6 style).

Used by the Outputs view's native file actions (desktop-only). Each function is a
thin per-OS dispatch with a graceful no-op fallback when no display is present
(headless VPS), mirroring jarvis/plugins/tool/app_resolver.py. Import-cleanliness
(HN-7): only stdlib at module scope; no platform-only package imported here.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.platform import detect_platform
from jarvis.platform.capabilities import detect_capabilities

log = logging.getLogger(__name__)


def open_file(path: Path) -> bool:
    """Open *path* with the OS default application.

    Returns True if a launcher was invoked, False on a headless host (no display)
    or on a launch error. Never raises.
    """
    if not detect_capabilities().display_present:
        log.info("open_file: no display present — skipping %s", path)
        return False
    plat = detect_platform()
    try:
        if plat == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
            return True
        cmd = ["open", str(path)] if plat == "darwin" else ["xdg-open", str(path)]
        subprocess.Popen(  # noqa: S603
            cmd, creationflags=NO_WINDOW_CREATIONFLAGS, close_fds=True
        )
        return True
    except OSError as exc:
        log.warning("open_file failed for %s: %s", path, exc)
        return False


def reveal_in_folder(path: Path) -> bool:
    """Open the OS file manager with *path* selected/highlighted.

    Returns True if a launcher was invoked, False on a headless host. Never raises.
    On Linux there is no portable "select the file" verb, so the containing folder
    is opened. On Windows, ``explorer /select,`` returns a non-zero exit code even
    on success — spawning it is treated as success, the exit code is ignored.
    """
    if not detect_capabilities().display_present:
        log.info("reveal_in_folder: no display present — skipping %s", path)
        return False
    plat = detect_platform()
    try:
        if plat == "win32":
            subprocess.Popen(  # noqa: S603
                ["explorer", "/select,", str(path)],
                creationflags=NO_WINDOW_CREATIONFLAGS,
                close_fds=True,
            )
            return True
        if plat == "darwin":
            subprocess.Popen(  # noqa: S603
                ["open", "-R", str(path)],
                creationflags=NO_WINDOW_CREATIONFLAGS,
                close_fds=True,
            )
            return True
        subprocess.Popen(  # noqa: S603
            ["xdg-open", str(path.parent)],
            creationflags=NO_WINDOW_CREATIONFLAGS,
            close_fds=True,
        )
        return True
    except OSError as exc:
        log.warning("reveal_in_folder failed for %s: %s", path, exc)
        return False


__all__ = ["open_file", "reveal_in_folder"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/platform/test_open_path.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Lint + commit**

Run: `ruff check jarvis/platform/open_path.py tests/unit/platform/test_open_path.py`
Expected: no errors.

```bash
git add jarvis/platform/open_path.py tests/unit/platform/test_open_path.py
git commit -m "feat(platform): cross-platform open_file/reveal_in_folder with headless no-op"
```

---

## Task 2: `markdown` dependency + `artifact_view.py` render helper

**Files:**
- Modify: `pyproject.toml` (add `markdown>=3.5` to base `dependencies`)
- Create: `jarvis/ui/web/artifact_view.py`
- Test: `tests/unit/ui/web/test_artifact_view.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/ui/web/test_artifact_view.py`:

```python
"""Unit tests for server-side artifact HTML rendering (markdown + escape + CSP)."""
from __future__ import annotations

import builtins

from jarvis.ui.web.artifact_view import VIEW_CSP, render_artifact_html


def test_markdown_renders_heading_to_html():
    out = render_artifact_html("report.md", "# Title\n\nHello")
    assert "<h1>Title</h1>" in out
    assert "Hello" in out
    assert "<!doctype html>" in out.lower()


def test_markdown_table_renders():
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    out = render_artifact_html("t.md", md)
    assert "<table>" in out


def test_non_markdown_is_escaped_pre():
    out = render_artifact_html("data.txt", "<script>alert(1)</script>")
    assert "<pre>" in out
    assert "&lt;script&gt;" in out
    assert "<script>alert(1)</script>" not in out


def test_markdown_missing_lib_falls_back_to_pre(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "markdown":
            raise ImportError("no markdown")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    out = render_artifact_html("report.md", "# Title")
    assert "<pre>" in out
    assert "# Title" in out  # raw, not rendered to <h1>


def test_csp_blocks_scripts():
    assert "default-src 'none'" in VIEW_CSP
    assert "script-src" not in VIEW_CSP
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/ui/web/test_artifact_view.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis.ui.web.artifact_view'`

- [ ] **Step 3: Install the dependency**

Add `"markdown>=3.5",` to the base `dependencies = [...]` list in `pyproject.toml` (next to
`"python-frontmatter>=1.1",`), with a comment:

```toml
    # Server-side markdown→HTML for the Outputs "view in browser" action
    # (pure-Python; the /view route degrades to <pre> if it is ever absent).
    "markdown>=3.5",
```

Then install it into the active interpreter:

Run: `C:\Program Files\Python311\python.exe -m pip install "markdown>=3.5"`
Expected: `Successfully installed markdown-...`

- [ ] **Step 4: Write minimal implementation**

Create `jarvis/ui/web/artifact_view.py`:

```python
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
VIEW_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:;"

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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/ui/web/test_artifact_view.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: Lint + commit**

Run: `ruff check jarvis/ui/web/artifact_view.py tests/unit/ui/web/test_artifact_view.py`
Expected: no errors.

```bash
git add jarvis/ui/web/artifact_view.py tests/unit/ui/web/test_artifact_view.py pyproject.toml
git commit -m "feat(outputs): server-side markdown→HTML artifact view renderer + markdown dep"
```

---

## Task 3: `_resolve_artifact_target` + `/download` endpoint

**Files:**
- Modify: `jarvis/ui/web/outputs_routes.py`
- Test: `tests/unit/ui/web/test_outputs_routes.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/ui/web/test_outputs_routes.py` (the `app` fixture + `TestClient` already
exist at the top of the file):

```python
def _make_deliverable(root: Path, mission_id: str, name: str, content: str) -> str:
    """Create tasks/<tid>/artifacts/files/<name> under mission_<id>; return rel path."""
    files_dir = (
        root / f"mission_{mission_id[:13]}" / "tasks" / "019edeadbeef"
        / "artifacts" / "files"
    )
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / name).write_text(content, encoding="utf-8")
    return f"tasks/019edeadbeef/artifacts/files/{name}"


def test_download_sets_attachment_disposition(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "report.md", "# Hi")
    client = TestClient(app)
    r = client.get(f"/api/outputs/{slug}/files/{rel}/download")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment")
    assert "report.md" in cd
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.text == "# Hi"


def test_download_inline_disposition(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "page.html", "<p>x</p>")
    client = TestClient(app)
    r = client.get(f"/api/outputs/{slug}/files/{rel}/download?disposition=inline")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("inline")


def test_download_blocks_non_deliverable(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "reflections.md").write_text("secret", encoding="utf-8")
    client = TestClient(app)
    r = client.get(f"/api/outputs/{slug}/files/reflections.md/download")
    assert r.status_code == 404


def test_download_blocks_path_traversal(app):
    slug = "mission_019ed2dfd0fab"
    (Path(app.state.outputs_root) / slug).mkdir(parents=True, exist_ok=True)
    client = TestClient(app)
    r = client.get(
        f"/api/outputs/{slug}/files/tasks/x/artifacts/files/..%2f..%2f..%2fsecret/download"
    )
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/ui/web/test_outputs_routes.py -q -k download`
Expected: FAIL with `404`/route-not-found (the `/download` route does not exist yet).

- [ ] **Step 3: Write minimal implementation**

In `jarvis/ui/web/outputs_routes.py`:

(a) Add imports near the top (after the existing `import time` / `from typing import Any`):

```python
import asyncio
import mimetypes

from starlette.responses import FileResponse, HTMLResponse
```

(b) Add the shared resolver just below `_is_deliverable_relpath` (after line ~404). It deliberately
returns **404 for a path escape** (the legacy `/raw` handler returns 400 and is left untouched to
keep its existing tests green):

```python
def _resolve_artifact_target(request: Request, slug: str, path: str) -> Path:
    """Resolve + allowlist-validate an artifact file path, or raise HTTPException.

    Mirrors the sandbox of the `/raw` handler: the slug stays inside the outputs
    root, the resolved file stays inside the session dir, and the relative path is
    a genuine deliverable (tasks/<id>/artifacts/files/**). Raises 404 (never 403,
    never confirming a scaffolding file exists) on any violation. Used by the
    download/view/reveal/open-native routes.
    """
    root = _outputs_root(request).resolve()
    base = (root / slug).resolve()
    try:
        base.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid slug") from exc
    target = (base / path).resolve()
    try:
        rel_parts = target.relative_to(base).parts
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail=f"unknown file: {path}"
        ) from exc
    if not _is_deliverable_relpath(rel_parts):
        raise HTTPException(status_code=404, detail=f"unknown file: {path}")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"unknown file: {path}")
    return target
```

(c) Add the `/download` route (place it just after the `/raw` handler, before `/{slug}/open`):

```python
@router.get("/{slug}/files/{path:path}/download")
async def download_output_artifact(
    slug: str, path: str, request: Request, disposition: str = "attachment"
) -> FileResponse:
    """Serve a single artifact file for download or inline viewing.

    ``disposition=attachment`` (default) → browser saves to Downloads;
    ``disposition=inline`` → browser renders natively (PDF/HTML/image/text).
    Streams the file (no 1 MiB inline ceiling). Sandboxed via the deliverable
    allowlist in ``_resolve_artifact_target``.
    """
    if disposition not in ("attachment", "inline"):
        disposition = "attachment"
    target = _resolve_artifact_target(request, slug, path)
    media_type, _ = mimetypes.guess_type(target.name)
    return FileResponse(
        target,
        media_type=media_type or "application/octet-stream",
        filename=target.name,
        content_disposition_type=disposition,
        headers={"X-Content-Type-Options": "nosniff"},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/ui/web/test_outputs_routes.py -q -k download`
Expected: PASS (4 passed)

- [ ] **Step 5: Run the full route module to catch regressions**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/ui/web/test_outputs_routes.py -q`
Expected: PASS (all existing + 4 new)

- [ ] **Step 6: Lint + commit**

Run: `ruff check jarvis/ui/web/outputs_routes.py`
Expected: no errors.

```bash
git add jarvis/ui/web/outputs_routes.py tests/unit/ui/web/test_outputs_routes.py
git commit -m "feat(outputs): artifact /download route (attachment|inline) + shared resolver"
```

---

## Task 4: `/view` endpoint (server-rendered Markdown)

**Files:**
- Modify: `jarvis/ui/web/outputs_routes.py`
- Test: `tests/unit/ui/web/test_outputs_routes.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/ui/web/test_outputs_routes.py`:

```python
def test_view_renders_markdown_with_csp(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "report.md", "# Heading\n\nbody")
    client = TestClient(app)
    r = client.get(f"/api/outputs/{slug}/files/{rel}/view")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<h1>Heading</h1>" in r.text
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_view_escapes_plain_text(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "x.txt", "<script>bad</script>")
    client = TestClient(app)
    r = client.get(f"/api/outputs/{slug}/files/{rel}/view")
    assert r.status_code == 200
    assert "&lt;script&gt;" in r.text
    assert "<script>bad</script>" not in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/ui/web/test_outputs_routes.py -q -k view`
Expected: FAIL (route missing → 404).

- [ ] **Step 3: Write minimal implementation**

In `jarvis/ui/web/outputs_routes.py`, add the import near the other imports:

```python
from jarvis.ui.web.artifact_view import VIEW_CSP, render_artifact_html
```

Add the route just after `download_output_artifact`:

```python
@router.get("/{slug}/files/{path:path}/view")
async def view_output_artifact(
    slug: str, path: str, request: Request
) -> HTMLResponse:
    """Render a text/markdown artifact as a standalone styled HTML page.

    Markdown is rendered to HTML; other text is shown escaped in <pre>. Carries a
    strict no-script CSP so artifact content can't execute JS in the app origin.
    The frontend only routes text/markdown here (binaries use /download?inline).
    """
    target = _resolve_artifact_target(request, slug, path)
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"read failed: {exc}"
        ) from exc
    return HTMLResponse(
        render_artifact_html(target.name, text),
        headers={
            "Content-Security-Policy": VIEW_CSP,
            "X-Content-Type-Options": "nosniff",
        },
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/ui/web/test_outputs_routes.py -q -k view`
Expected: PASS (2 passed)

- [ ] **Step 5: Lint + commit**

Run: `ruff check jarvis/ui/web/outputs_routes.py`
Expected: no errors.

```bash
git add jarvis/ui/web/outputs_routes.py tests/unit/ui/web/test_outputs_routes.py
git commit -m "feat(outputs): artifact /view route renders markdown to a CSP-locked HTML page"
```

---

## Task 5: `/capabilities` endpoint + launcher flag

**Files:**
- Modify: `jarvis/ui/web/outputs_routes.py`
- Modify: `jarvis/ui/web/launcher.py:206` (in `_run_headless`)
- Modify: `jarvis/ui/desktop_app.py:635` (desktop backend wiring)
- Test: `tests/unit/ui/web/test_outputs_routes.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/ui/web/test_outputs_routes.py`:

```python
def test_capabilities_reports_flag_true(app):
    app.state.native_file_actions = True
    client = TestClient(app)
    r = client.get("/api/outputs/capabilities")
    assert r.status_code == 200
    data = r.json()
    assert data["native_file_actions"] is True
    assert data["platform"] in ("win32", "darwin", "linux")


def test_capabilities_defaults_false_when_unset(app):
    # The fixture app never sets the flag — must default to False (VPS-safe).
    client = TestClient(app)
    r = client.get("/api/outputs/capabilities")
    assert r.status_code == 200
    assert r.json()["native_file_actions"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/ui/web/test_outputs_routes.py -q -k capabilities`
Expected: FAIL (route missing → 404).

- [ ] **Step 3: Write minimal implementation**

(a) In `jarvis/ui/web/outputs_routes.py`, add the import:

```python
from jarvis.platform import detect_platform
```

Add the route immediately after the `list_outputs` handler (so it reads top-of-file; it never
collides with `/{slug}/...` since `capabilities` is a single literal segment):

```python
@router.get("/capabilities")
def outputs_capabilities(request: Request) -> dict[str, Any]:
    """Report whether native file actions (reveal / open-with-default-app) work here.

    True only on a local desktop run (set by the launcher); False on a headless VPS
    where opening a file would target the *server's* desktop, not the user's. The
    frontend hides the native buttons when this is False; the routes 404 too.
    """
    native = bool(getattr(request.app.state, "native_file_actions", False))
    return {"native_file_actions": native, "platform": detect_platform()}
```

(b) In `jarvis/ui/web/launcher.py`, in `_run_headless`, next to the other `server.app.state.*`
assignments (around line 206–208), add:

```python
    # Headless/VPS: native file actions would open on the SERVER's desktop, not
    # the user's. Disable them (the frontend hides the buttons; the routes 404).
    server.app.state.native_file_actions = False
```

(c) In `jarvis/ui/desktop_app.py`, next to `server.app.state.desktop_app = self` (around line 635),
add:

```python
        # Local desktop run: the user IS at this machine, so reveal/open-with-
        # default-app target their own desktop. Enable the native file actions.
        server.app.state.native_file_actions = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/ui/web/test_outputs_routes.py -q -k capabilities`
Expected: PASS (2 passed)

- [ ] **Step 5: Lint + commit**

Run: `ruff check jarvis/ui/web/outputs_routes.py jarvis/ui/web/launcher.py jarvis/ui/desktop_app.py`
Expected: no errors.

```bash
git add jarvis/ui/web/outputs_routes.py jarvis/ui/web/launcher.py jarvis/ui/desktop_app.py tests/unit/ui/web/test_outputs_routes.py
git commit -m "feat(outputs): /capabilities endpoint + native_file_actions launcher flag (VPS-safe default)"
```

---

## Task 6: `/reveal` + `/open-native` endpoints (local-only)

**Files:**
- Modify: `jarvis/ui/web/outputs_routes.py`
- Test: `tests/unit/ui/web/test_outputs_routes.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/ui/web/test_outputs_routes.py`:

```python
from unittest.mock import patch  # add to the existing imports at top if not present


def test_reveal_404_when_native_disabled(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "report.md", "# Hi")
    app.state.native_file_actions = False
    client = TestClient(app)
    r = client.post(f"/api/outputs/{slug}/files/{rel}/reveal")
    assert r.status_code == 404


def test_reveal_calls_platform_when_enabled(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "report.md", "# Hi")
    app.state.native_file_actions = True
    client = TestClient(app)
    with patch("jarvis.platform.open_path.reveal_in_folder", return_value=True) as rev:
        r = client.post(f"/api/outputs/{slug}/files/{rel}/reveal")
    assert r.status_code == 200
    assert r.json()["opened"] is True
    rev.assert_called_once()


def test_open_native_calls_platform_when_enabled(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "report.md", "# Hi")
    app.state.native_file_actions = True
    client = TestClient(app)
    with patch("jarvis.platform.open_path.open_file", return_value=True) as opn:
        r = client.post(f"/api/outputs/{slug}/files/{rel}/open-native")
    assert r.status_code == 200
    assert r.json()["opened"] is True
    opn.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/ui/web/test_outputs_routes.py -q -k "reveal or open_native"`
Expected: FAIL (routes missing → 404 for the "enabled" cases too).

- [ ] **Step 3: Write minimal implementation**

In `jarvis/ui/web/outputs_routes.py`, add both routes after `view_output_artifact`. The
`open_path` functions are imported lazily inside the handler so the module still imports cleanly on
a runtime without them and `patch(...)` targets the canonical module path:

```python
@router.post("/{slug}/files/{path:path}/reveal")
async def reveal_output_artifact(
    slug: str, path: str, request: Request
) -> dict[str, Any]:
    """Open the OS file manager with the artifact selected. Local desktop only."""
    if not getattr(request.app.state, "native_file_actions", False):
        raise HTTPException(status_code=404, detail="native file actions unavailable")
    target = _resolve_artifact_target(request, slug, path)
    from jarvis.platform import open_path

    opened = await asyncio.to_thread(open_path.reveal_in_folder, target)
    return {"opened": bool(opened), "path": str(target)}


@router.post("/{slug}/files/{path:path}/open-native")
async def open_output_artifact_native(
    slug: str, path: str, request: Request
) -> dict[str, Any]:
    """Open the artifact with the OS default application. Local desktop only."""
    if not getattr(request.app.state, "native_file_actions", False):
        raise HTTPException(status_code=404, detail="native file actions unavailable")
    target = _resolve_artifact_target(request, slug, path)
    from jarvis.platform import open_path

    opened = await asyncio.to_thread(open_path.open_file, target)
    return {"opened": bool(opened), "path": str(target)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/ui/web/test_outputs_routes.py -q -k "reveal or open_native"`
Expected: PASS (3 passed)

- [ ] **Step 5: Full module + lint + commit**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/ui/web/test_outputs_routes.py -q`
Expected: PASS (all)

Run: `ruff check jarvis/ui/web/outputs_routes.py`
Expected: no errors.

```bash
git add jarvis/ui/web/outputs_routes.py tests/unit/ui/web/test_outputs_routes.py
git commit -m "feat(outputs): native reveal/open-with-default-app routes (404 unless local desktop)"
```

---

## Task 7: Frontend helpers + `useOutputsCapabilities`

**Files:**
- Modify: `jarvis/ui/web/frontend/src/hooks/useOutputs.ts`
- Test: `jarvis/ui/web/frontend/src/hooks/useOutputs.classify.test.ts`

- [ ] **Step 1: Write the failing test**

Create `jarvis/ui/web/frontend/src/hooks/useOutputs.classify.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import {
  artifactDownloadUrl,
  artifactOpenUrl,
  classifyArtifact,
} from "./useOutputs";

describe("classifyArtifact", () => {
  it("classifies markdown/text/code as rendered", () => {
    expect(classifyArtifact("a/b/report.md")).toBe("rendered");
    expect(classifyArtifact("notes.txt")).toBe("rendered");
    expect(classifyArtifact("main.py")).toBe("rendered");
  });
  it("classifies pdf/html/images as inline", () => {
    expect(classifyArtifact("doc.pdf")).toBe("inline");
    expect(classifyArtifact("page.html")).toBe("inline");
    expect(classifyArtifact("pic.PNG")).toBe("inline");
  });
  it("classifies unknown binaries as opaque", () => {
    expect(classifyArtifact("archive.zip")).toBe("opaque");
    expect(classifyArtifact("blob.bin")).toBe("opaque");
  });
});

describe("artifact URLs", () => {
  const slug = "mission_abc";
  const path = "tasks/x/artifacts/files/report.md";
  it("builds an attachment download URL", () => {
    expect(artifactDownloadUrl(slug, path)).toBe(
      `/api/outputs/${slug}/files/${encodeURI(path)}/download?disposition=attachment`,
    );
  });
  it("routes markdown open to /view", () => {
    expect(artifactOpenUrl(slug, path)).toBe(
      `/api/outputs/${slug}/files/${encodeURI(path)}/view`,
    );
  });
  it("routes pdf open to inline download", () => {
    const p = "tasks/x/artifacts/files/doc.pdf";
    expect(artifactOpenUrl(slug, p)).toBe(
      `/api/outputs/${slug}/files/${encodeURI(p)}/download?disposition=inline`,
    );
  });
  it("returns null open URL for opaque files", () => {
    expect(artifactOpenUrl(slug, "tasks/x/artifacts/files/a.zip")).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `jarvis/ui/web/frontend/`): `npm run test -- useOutputs.classify`
Expected: FAIL — `classifyArtifact`/`artifactOpenUrl`/`artifactDownloadUrl` are not exported.

- [ ] **Step 3: Write minimal implementation**

Append to `jarvis/ui/web/frontend/src/hooks/useOutputs.ts`:

```ts
export interface OutputsCapabilities {
  native_file_actions: boolean;
  platform: "win32" | "darwin" | "linux";
}

export function useOutputsCapabilities() {
  return useQuery<OutputsCapabilities>({
    queryKey: ["outputs-capabilities"],
    queryFn: async () => {
      const r = await fetch("/api/outputs/capabilities");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    staleTime: 60_000,
  });
}

// --- Artifact download / open URL helpers ---------------------------------

export function artifactDownloadUrl(slug: string, path: string): string {
  return `/api/outputs/${slug}/files/${encodeURI(
    path,
  )}/download?disposition=attachment`;
}

export type ArtifactOpenKind = "rendered" | "inline" | "opaque";

const _INLINE_EXT = [
  ".pdf", ".html", ".htm", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
];
const _RENDERED_EXT = [
  ".md", ".markdown", ".txt", ".json", ".jsonl", ".csv", ".yaml", ".yml",
  ".toml", ".log", ".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".sh", ".ps1",
];

/** Decide how an artifact opens in the browser, by extension. */
export function classifyArtifact(name: string): ArtifactOpenKind {
  const lower = name.toLowerCase();
  if (_INLINE_EXT.some((e) => lower.endsWith(e))) return "inline";
  if (_RENDERED_EXT.some((e) => lower.endsWith(e))) return "rendered";
  return "opaque";
}

/** The URL the "open in browser" button targets, or null for opaque files. */
export function artifactOpenUrl(slug: string, path: string): string | null {
  const kind = classifyArtifact(path);
  const enc = encodeURI(path);
  if (kind === "rendered") return `/api/outputs/${slug}/files/${enc}/view`;
  if (kind === "inline")
    return `/api/outputs/${slug}/files/${enc}/download?disposition=inline`;
  return null;
}

export async function revealArtifact(slug: string, path: string): Promise<void> {
  const r = await fetch(
    `/api/outputs/${slug}/files/${encodeURI(path)}/reveal`,
    { method: "POST" },
  );
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}

export async function openArtifactNative(
  slug: string,
  path: string,
): Promise<void> {
  const r = await fetch(
    `/api/outputs/${slug}/files/${encodeURI(path)}/open-native`,
    { method: "POST" },
  );
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `jarvis/ui/web/frontend/`): `npm run test -- useOutputs.classify`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/hooks/useOutputs.ts jarvis/ui/web/frontend/src/hooks/useOutputs.classify.test.ts
git commit -m "feat(outputs-ui): capabilities hook + artifact download/open URL helpers"
```

---

## Task 8: Action buttons in `ArtifactRow`

**Files:**
- Modify: `jarvis/ui/web/frontend/src/views/OutputsView.tsx`

**Note on UI-string language:** per the repo's Output Language Policy, *new* UI strings must be
English source (the CI `language-policy` gate blocks newly added German lines). Use English `title`
attributes ("Download", "Open in browser", "Open with default app", "Reveal in folder") even though
the surrounding (grandfathered) strings are German.

**Note on testing:** `ArtifactRow` is a private (non-exported) sub-component, so its DOM is verified
by the live drive in Task 9 plus the type-check/build below. The risky logic (URL building,
classification) is already fully unit-tested in Task 7.

- [ ] **Step 1: Add icon imports**

In `OutputsView.tsx`, extend the existing `lucide-react` import to include the new icons. Find the
import that currently brings in `ChevronDown, ChevronRight, FileText, Loader2` and add:

```tsx
import {
  // ...existing icons...
  Download,
  ExternalLink,
  FolderOpen,
  AppWindow,
} from "lucide-react";
```

- [ ] **Step 2: Import the new hook + helpers**

Find the existing import from `../hooks/useOutputs` (the one bringing in `useArtifactsForOutput`,
`useArtifactFile`, `ArtifactSummary`) and add:

```tsx
import {
  // ...existing...
  useOutputsCapabilities,
  artifactDownloadUrl,
  artifactOpenUrl,
  revealArtifact,
  openArtifactNative,
} from "../hooks/useOutputs";
```

- [ ] **Step 3: Fetch capabilities in `ArtifactsSection` and pass down**

In `ArtifactsSection` (around line 461), after `const q = useArtifactsForOutput(slug);` add:

```tsx
  const caps = useOutputsCapabilities();
  const nativeActions = caps.data?.native_file_actions ?? false;
```

And change the row render (around line 497) to pass the flag:

```tsx
          {files.map((f) => (
            <ArtifactRow
              key={f.path}
              slug={slug}
              file={f}
              nativeActions={nativeActions}
            />
          ))}
```

- [ ] **Step 4: Add the `nativeActions` prop + action cluster to `ArtifactRow`**

Change the `ArtifactRow` signature (line ~505) to accept `nativeActions`:

```tsx
function ArtifactRow({
  slug,
  file,
  nativeActions,
}: {
  slug: string;
  file: ArtifactSummary;
  nativeActions: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const full = useArtifactFile(slug, expanded ? file.path : null);
  const fileName = file.path.split("/").pop() ?? file.path;
  const openUrl = artifactOpenUrl(slug, file.path);
```

Replace the existing header `<button>...</button>` (lines ~528–544 — the single toggle button with
the chevron, file path, and size label) with this header row (the toggle stays a button, the actions
become siblings so we never nest interactive elements):

```tsx
      <div className="flex w-full items-center gap-1 px-3 py-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left hover:bg-secondary/30"
        >
          {expanded ? (
            <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" />
          )}
          <span className="min-w-0 flex-1 truncate font-mono text-[11px]">
            {file.path}
          </span>
        </button>
        <span className="shrink-0 text-[10px] text-muted-foreground">
          {sizeLabel}
        </span>
        <div className="flex shrink-0 items-center gap-0.5">
          <a
            href={artifactDownloadUrl(slug, file.path)}
            download={fileName}
            title="Download"
            onClick={(e) => e.stopPropagation()}
            className="rounded p-1 hover:bg-secondary/40"
          >
            <Download className="h-3.5 w-3.5 text-muted-foreground" />
          </a>
          {openUrl && (
            <button
              type="button"
              title="Open in browser"
              onClick={() => window.open(openUrl, "_blank", "noopener")}
              className="rounded p-1 hover:bg-secondary/40"
            >
              <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
            </button>
          )}
          {nativeActions && (
            <>
              <button
                type="button"
                title="Open with default app"
                onClick={() =>
                  void openArtifactNative(slug, file.path).catch(() => {})
                }
                className="rounded p-1 hover:bg-secondary/40"
              >
                <AppWindow className="h-3.5 w-3.5 text-muted-foreground" />
              </button>
              <button
                type="button"
                title="Reveal in folder"
                onClick={() =>
                  void revealArtifact(slug, file.path).catch(() => {})
                }
                className="rounded p-1 hover:bg-secondary/40"
              >
                <FolderOpen className="h-3.5 w-3.5 text-muted-foreground" />
              </button>
            </>
          )}
        </div>
      </div>
```

Leave the `{expanded && (...)}` preview block below unchanged.

- [ ] **Step 5: Type-check + build**

Run (from `jarvis/ui/web/frontend/`): `npx tsc --noEmit`
Expected: no errors.

Run: `npm run build`
Expected: build succeeds, emits to `jarvis/ui/web/dist`.

- [ ] **Step 6: Re-run the frontend test suite for regressions**

Run (from `jarvis/ui/web/frontend/`): `npm run test`
Expected: PASS (existing OutputsView tests + the new classify test).

- [ ] **Step 7: Commit**

```bash
git add jarvis/ui/web/frontend/src/views/OutputsView.tsx jarvis/ui/web/dist
git commit -m "feat(outputs-ui): per-artifact download / open / reveal / open-native buttons"
```

---

## Task 9: Install, wire, and live-verify

**Files:** none (integration + verification)

- [ ] **Step 1: Ensure the dependency + editable install are active**

Run:
```bash
C:\Program Files\Python311\python.exe -m pip install "markdown>=3.5"
C:\Program Files\Python311\python.exe -m pip install -e . --no-deps
```
Expected: markdown present; entry points refreshed.

- [ ] **Step 2: Full backend test sweep for the touched areas**

Run:
```bash
C:\Program Files\Python311\python.exe -m pytest tests/unit/platform/test_open_path.py tests/unit/ui/web/test_artifact_view.py tests/unit/ui/web/test_outputs_routes.py -q
```
Expected: all PASS.

- [ ] **Step 3: Restart the app to load the new routes**

The editable install picks up Python changes, but the running process must reload. Restart via:
`POST http://127.0.0.1:47821/api/settings/restart-app` (NOT `Stop-Process` — Access Denied under
the tray `pythonw.exe`). If the app is not running, launch it: `run.bat`.

- [ ] **Step 4: Live-verify download + view (the pywebview unknown)**

In the Outputs view, open a session with a `.md` artifact (e.g. the `market-research-summary.md`
deliverable). Verify, recording the result in the SIGNOFF notes:
1. **Download** button → file lands in the browser's Downloads folder. In the **desktop pywebview**
   window specifically, confirm the `<a download>` actually saves the file. If pywebview swallows it
   (the documented risk), the user still has "Open with default app" / "Reveal in folder" — note the
   behavior either way.
2. **Open in browser** on the `.md` → a new tab shows the rendered HTML (headings/tables), not raw
   `#` text, and DevTools shows the `Content-Security-Policy` header.
3. On the **local desktop run**, the **Open with default app** and **Reveal in folder** buttons are
   present and work; confirm they are **absent** when hitting the app via `--headless` (VPS path).

- [ ] **Step 5: Record verification outcome**

Note the pywebview download result and the macOS/Linux native-action status (still
`unverified-on-real-desktop` unless run on those OSes) in the session summary / SIGNOFF-LOG, per the
cross-platform honesty rule. Do not claim macOS/Linux "live-verified" from a Windows box.

---

## Self-Review (completed by plan author)

**Spec coverage:**
- D1 hybrid model → Tasks 3 (download), 5 (capabilities/flag), 6 (native), 8 (gated buttons). ✓
- D2 server-rendered markdown → Tasks 2 (renderer), 4 (/view). ✓
- D3 scope = Outputs view only → Tasks 7–8 confine UI to `useOutputs.ts` + `OutputsView.tsx`. ✓
- Path-traversal / allowlist reuse → Task 3 `_resolve_artifact_target` + tests. ✓
- XSS CSP on /view, nosniff on download → Tasks 2/4 + tests. ✓
- Cross-platform open/reveal with headless no-op → Task 1 + tests. ✓
- VPS-safe default (flag False) → Task 5 + `test_capabilities_defaults_false_when_unset`. ✓
- pywebview unknown → Task 9 live verification. ✓
- `markdown` base dep + graceful degrade → Task 2 + `test_markdown_missing_lib_falls_back_to_pre`. ✓

**Placeholder scan:** No TBD/TODO; every code + test step shows complete content. ✓

**Type/name consistency:** `_resolve_artifact_target`, `native_file_actions`, `classifyArtifact`,
`artifactOpenUrl`, `artifactDownloadUrl`, `revealArtifact`, `openArtifactNative`, `render_artifact_html`,
`VIEW_CSP`, `open_file`, `reveal_in_folder` are used identically across backend, frontend, and tests. ✓
