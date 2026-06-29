# Artifact Download & View — Design

**Date:** 2026-06-16
**Status:** Approved (design phase) — ready for implementation plan
**Topic:** Let the user get a Jarvis/Jarvis-Agent output file *out* of the app — download it to the
Downloads folder and open/view it in the browser — cross-platform (Windows, macOS, Linux, and the
headless VPS-browser runtime).

---

## 1. Problem

The Outputs view (`jarvis/ui/web/frontend/src/views/OutputsView.tsx`, component `ArtifactRow`,
~lines 505–577) renders deliverable artifact files such as
`tasks/<task_id>/artifacts/files/market-research-summary.md` **only as an in-app text preview**.
There is no way to get the file out except manually selecting and copying the text and pasting it
into another app (Google Docs/Sheets, etc.).

The backend (`jarvis/ui/web/outputs_routes.py`) only ever returns artifact bytes wrapped in **JSON**
(`GET /{slug}/files/{path}/raw` → `{"text": ...}`, 1 MiB inline ceiling). There is **no** real file
endpoint with a `Content-Disposition` header anywhere in the web layer. The only existing "get it
out" affordance is `POST /{slug}/open`, which opens the *session folder* in the file manager and is
**Windows-only**.

The user wants, for any artifact regardless of type (`.md`, PDF, HTML, image, …):

1. **Download** — one click puts the file in the Downloads folder.
2. **Open / view** — one click opens it in the browser; Markdown rendered nicely, not raw.
3. Works the same on **Windows, macOS, and Linux**, and also for a user driving Jarvis purely
   through a remote browser against a VPS.

---

## 2. Decisions (locked by brainstorming)

- **D1 — Hybrid download model ("both combined").** A universal browser-native path that works
  everywhere (real browser *and* VPS), **plus** native OS conveniences ("reveal in folder", "open
  with default app") that appear **only when Jarvis runs locally as a desktop app**.
- **D2 — Markdown is server-rendered.** The "view" action opens a real new browser tab with a
  standalone, styled HTML page; Markdown is rendered to HTML server-side with the small pure-Python
  `markdown` library. PDF/HTML/images open inline natively; plain text/code as a plaintext tab.
- **D3 — Scope is the Outputs view only.** No chat-attachment / Wiki / Docs download in this
  iteration (YAGNI). The backend endpoints are generic enough to be reused later, but the UI work
  is confined to `OutputsView.tsx`.

---

## 3. Architecture overview

```
ArtifactRow (OutputsView.tsx)
   │  ⬇ Download   ──>  <a download href="/api/outputs/{slug}/files/{path}/download?disposition=attachment">
   │  ↗ Open       ──>  window.open( view-or-inline URL )
   │  📂 Reveal     ──>  POST /api/outputs/{slug}/files/{path}/reveal      (local-only)
   │  ▶ Open native ──>  POST /api/outputs/{slug}/files/{path}/open-native (local-only)
   │
   └─ useOutputsCapabilities()  ──>  GET /api/outputs/capabilities  →  {native_file_actions, platform}

outputs_routes.py  (reuses existing _is_deliverable_relpath allowlist + _outputs_root resolver)
   ├─ GET  /{slug}/files/{path}/download   FileResponse, Content-Disposition attachment|inline
   ├─ GET  /{slug}/files/{path}/view       standalone styled HTML (server-rendered markdown)
   ├─ POST /{slug}/files/{path}/reveal     jarvis.platform.open_path.reveal_in_folder  (local-only)
   ├─ POST /{slug}/files/{path}/open-native jarvis.platform.open_path.open_file        (local-only)
   └─ GET  /capabilities                   {native_file_actions, platform}

jarvis/platform/open_path.py   (new, cross-platform, isolated, unit-tested)
   ├─ open_file(path)         os.startfile / open / xdg-open
   └─ reveal_in_folder(path)  explorer /select, / open -R / xdg-open <dir>

launcher.py
   ├─ _run_desktop()   → app.state.native_file_actions = True
   └─ _run_headless()  → app.state.native_file_actions = False   (safe VPS default)
```

---

## 4. Components

### 4.1 Backend — `jarvis/ui/web/outputs_routes.py`

All new routes **reuse the existing, already-hardened helpers**:
- `_outputs_root(request)` — resolves the artifacts base dir from `app.state.outputs_root`.
- `_is_deliverable_relpath(rel)` — the allowlist that restricts surfacing to
  `tasks/<task_id>/artifacts/files/<rel>` and blocks `claude_config/`, `.codex/`,
  `openclaw_state/`, `diff*.patch`, `logs/`, `reflections.md`.
- The same path-resolution + `Path.resolve()`-is-inside-root check used by the raw endpoint.

A shared internal resolver `_resolve_artifact_target(request, slug, path) -> Path` is extracted
(refactored out of the existing `/raw` handler if duplicated) so download/view/reveal/open-native
all validate identically. **No file is ever served whose resolved path escapes the session's
`artifacts/files/` directory.**

**`GET /{slug}/files/{path}/download?disposition=attachment|inline`**
- `disposition` defaults to `attachment`.
- Returns `starlette.responses.FileResponse(target, media_type=<guessed>, filename=<basename>,
  content_disposition_type=disposition)`. Starlette emits an RFC 6266 / 5987-correct
  `Content-Disposition` (handles non-ASCII filenames via `filename*=UTF-8''…`).
- `media_type` via `mimetypes.guess_type`; fall back to `application/octet-stream`.
- Adds `X-Content-Type-Options: nosniff`.
- Streams the file (no in-memory read; the 1 MiB ceiling of `/raw` does **not** apply).

**`GET /{slug}/files/{path}/view`**
- For Markdown (`.md`, `.markdown`): render to HTML with `markdown` + extensions
  `["extra", "sane_lists", "tables", "fenced_code"]` (no Pygments dependency), wrap in a minimal
  self-contained HTML document with embedded CSS (GitHub-ish readable style), return `text/html`.
- For other text/code: return the content inside `<pre>` (escaped), same shell.
- **Graceful degradation:** if `markdown` is not importable, render the raw markdown inside `<pre>`
  so the base install never hard-fails (English log line: "markdown lib unavailable; serving raw").
- **CSP:** the response carries `Content-Security-Policy: default-src 'none'; style-src
  'unsafe-inline'; img-src 'self' data:;` — no scripts execute, neutralizing XSS from a malicious
  or hallucinated artifact even though it renders in the app origin.
- Non-text binary types are not handled by `/view` (the frontend never routes them here; it uses
  `/download?disposition=inline` for PDF/HTML/images).

**`POST /{slug}/files/{path}/reveal`** and **`POST /{slug}/files/{path}/open-native`**
- Guard: if `app.state.native_file_actions` is falsy → `HTTP 404` (feature absent on this runtime).
  Defense-in-depth on top of the frontend hiding the buttons.
- Resolve + allowlist-validate the path, then call `jarvis.platform.open_path.reveal_in_folder` /
  `open_file`. Return `{"opened": true, "path": "..."}` or a structured failure.
- These never block the event loop meaningfully (fire-and-forget subprocess); wrap in a thread/executor
  if needed to keep the handler non-blocking.

**`GET /capabilities`**
- Returns `{"native_file_actions": bool(app.state.native_file_actions), "platform": detect_platform()}`.

### 4.2 Cross-platform core — `jarvis/platform/open_path.py` (new)

Two functions, each a thin, isolated, per-OS dispatch with a graceful no-op fallback. Mirrors the
shape of `jarvis/plugins/tool/app_resolver.py`.

```
def open_file(path: Path) -> bool:
    """Open a file with the OS default application. Returns True if launched."""
    # win32:  os.startfile(path)
    # darwin: subprocess.Popen(["open", str(path)], ...)
    # linux:  subprocess.Popen(["xdg-open", str(path)], ...)

def reveal_in_folder(path: Path) -> bool:
    """Open the file manager with the file selected/highlighted. Returns True if launched."""
    # win32:  subprocess.Popen(["explorer", "/select,", str(path)])    (note: explorer quirks)
    # darwin: subprocess.Popen(["open", "-R", str(path)])
    # linux:  subprocess.Popen(["xdg-open", str(path.parent)])         (no portable "select")
```

Rules:
- Every `subprocess.Popen` passes `creationflags=NO_WINDOW_CREATIONFLAGS`
  (`jarvis.core.process_utils`) — AP-1.
- `shell=False` always; the path is passed as an argv element, never interpolated into a string.
- If `detect_capabilities().display_present` is false (headless) → log an English line and return
  `False` (no-op). The HTTP layer already 404s these on a headless runtime, so this is belt-and-suspenders.
- Windows `explorer /select,` returns exit code 1 even on success — do **not** treat non-zero exit
  as failure for the reveal path; just confirm the process spawned.

### 4.3 Launcher flag — `jarvis/ui/web/launcher.py`

- `_run_desktop(...)` sets `app.state.native_file_actions = True` after the app is built.
- `_run_headless(...)` sets `app.state.native_file_actions = False`.
- Default if unset anywhere: **`False`** (VPS-safe — native actions are opt-in, never leak onto a
  server runtime where the file would open on the *server's* desktop, not the user's).

### 4.4 Frontend — `OutputsView.tsx` + `useOutputs.ts`

- New hook `useOutputsCapabilities()` in `hooks/useOutputs.ts` (React Query, same `fetch` pattern as
  the others) → `GET /api/outputs/capabilities`.
- `ArtifactRow` gains an action cluster:
  - **Download:** an `<a download href={downloadUrl}>` styled as an icon button. Pure anchor, no JS
    blob — the most robust path; real browsers save straight to Downloads.
  - **Open:** a button calling `window.open(openUrl, "_blank")`. The target is chosen by a small
    extension→class map (`classifyArtifact(name)`), with three classes:
    - **rendered** (`.md`, `.markdown`, `.txt`, `.json`, `.csv`, code) → `/view`
    - **inline** (`.pdf`, `.html`, `.htm`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.svg`) →
      `/download?disposition=inline` (browser renders natively)
    - **opaque** (anything else, e.g. `.zip`, `.bin`, `.xlsx`) → **no Open button**, only Download
  - **Reveal in folder** + **Open with default app:** shown only when
    `capabilities.native_file_actions` is true; POST to the respective routes.
- URL building helper colocated with the existing artifact hooks; paths are `encodeURI`-d exactly as
  the current `useArtifactFile` does.

---

## 5. Data flow

1. User opens a session in Outputs → `useArtifactsForOutput(slug)` lists files (unchanged).
2. `useOutputsCapabilities()` resolves once → decides whether native buttons render.
3. **Download:** browser GETs `/download?disposition=attachment` → `FileResponse` with
   `Content-Disposition: attachment` → browser writes to Downloads. No app state changes.
4. **Open (markdown):** new tab GETs `/view` → server renders MD→HTML (CSP-locked) → browser shows it.
5. **Open (pdf/html/image):** new tab GETs `/download?disposition=inline` → browser renders natively.
6. **Reveal / open-native (local only):** POST → `jarvis.platform.open_path` spawns the OS tool.

---

## 6. Error handling & security

- **Path traversal:** every endpoint resolves through the shared `_resolve_artifact_target` and
  re-checks `_is_deliverable_relpath` + that `target.resolve()` is inside the session root. A `..`
  or absolute-path attempt → `HTTP 404` (not 403, to avoid confirming existence). This is the single
  most important invariant — `FileResponse` will happily serve anything you point it at.
- **XSS via `/view`:** strict CSP (`default-src 'none'`, no scripts) on the rendered markdown page.
- **XSS via inline HTML artifacts:** opening an agent-authored `.html` inline executes its JS in the
  app origin. Mitigations: `X-Content-Type-Options: nosniff`; this is a **documented residual risk**
  accepted for the single-user desktop/self-hosted context (the user explicitly asked to *view* HTML
  artifacts). A future hardening option is to serve inline HTML from a sandboxed sub-origin; out of
  scope here, noted in §8.
- **Native actions on a VPS:** structurally impossible — `native_file_actions=False` by default and
  the routes 404. A file can never be opened on the server's desktop by a remote user.
- **Missing `markdown` lib:** `/view` degrades to `<pre>` raw text; never a 500.
- **Large files:** `FileResponse` streams; no memory blow-up. (No artificial size cap added; the
  deliverable allowlist already bounds what is reachable.)
- **pywebview download behavior (the one real unknown):** an `<a download>` in the embedded webview
  is not guaranteed to land in Downloads on every pywebview version. This is exactly why D1 (hybrid)
  is correct — the desktop user falls back to "open with default app" / "reveal in folder", and the
  VPS browser user has the clean `<a download>` regardless. **Verified live after implementation.**

---

## 7. Dependencies

- Add `markdown` (pure-Python, no native/Windows/GPU deps) to the **base** runtime requirements —
  it satisfies the cloud-first doctrine (boots on `python:3.11-slim`). The `/view` route degrades
  gracefully if it is somehow absent, so it is not a hard boot dependency.
- No new frontend dependency (`react-markdown` already present is *not* used here, per D2).

---

## 8. Testing

**Backend (`tests/unit/ui/web/` or the outputs-routes test module):**
- `download` sets `Content-Disposition: attachment` with the correct filename; `disposition=inline`
  sets `inline`; correct `media_type` per extension; `nosniff` present.
- Path traversal (`../../etc/passwd`, absolute path, a blocked `reflections.md`) → 404.
- `/view` renders markdown headings/tables to HTML; carries the strict CSP header; degrades to
  `<pre>` when `markdown` import is monkpatched away.
- `reveal` / `open-native` → 404 when `app.state.native_file_actions` is False; call the platform
  function (mocked) when True.
- `/capabilities` reflects the flag and platform.

**Cross-platform (`tests/unit/platform/`):**
- `open_file` / `reveal_in_folder` select the right argv per `sys.platform` (parametrized, subprocess
  mocked); pass `NO_WINDOW_CREATIONFLAGS`; `shell=False`.
- No-op + False return when `display_present` is False.
- Windows reveal tolerates a non-zero `explorer` exit code.

**Frontend (`vitest`):**
- Action buttons render for an artifact row; download anchor has `download` + correct href.
- Native buttons hidden when `native_file_actions` is False, shown when True.
- Open routes to `/view` for `.md` and `/download?disposition=inline` for `.pdf`.

---

## 9. Out of scope / future

- Chat-attachment, Wiki, and Docs download (same backend pattern, different UI surface).
- Sandboxed sub-origin for inline HTML artifact rendering (XSS hardening beyond CSP/nosniff).
- "Download all artifacts as .zip" for a session.
- macOS/Linux native-action **live GUI sign-off** — per the cross-platform SIGNOFF-LOG, these remain
  `unverified-on-real-desktop` until run on a real device; CI only proves argv selection.

---

## 10. File touch list (for the implementation plan)

- `jarvis/platform/open_path.py` — **new** (open_file, reveal_in_folder).
- `jarvis/ui/web/outputs_routes.py` — new routes + shared `_resolve_artifact_target`.
- `jarvis/ui/web/launcher.py` — set `app.state.native_file_actions` in both run paths.
- `jarvis/ui/web/frontend/src/hooks/useOutputs.ts` — `useOutputsCapabilities` + URL helpers.
- `jarvis/ui/web/frontend/src/views/OutputsView.tsx` — action cluster in `ArtifactRow`.
- `pyproject.toml` / requirements — add `markdown` to base.
- Tests as per §8.
