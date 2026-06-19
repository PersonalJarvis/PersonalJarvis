# Socials Section — Design Spec

**Date:** 2026-06-01
**Status:** Approved (build now, v1)
**Author:** Claude (brainstorming session with maintainer)

## Goal

A new **Socials** navigation section, pinned at the very bottom of the desktop-app
sidebar, that displays the project's social-media links as cards (real brand
logos, open externally). The list is **editable inside the app** (add / edit /
delete), persisted server-side so it survives restarts and is identical across
the pywebview desktop runtime and the browser runtime.

First seed (on first run, when no data file exists yet):

1. **Discord** — `https://discord.gg/UPu6pFWrJ` (top)
2. **GitHub (Repo)** — `https://github.com/PersonalJarvis/PersonalJarvis`
3. **GitHub (Profile)** — placeholder `https://github.com/PersonalJarvis`
   (the maintainer corrects this to their real handle via the UI)

## Non-goals (v1)

- Drag-to-reorder — **deferred to v2**. v1 renders in stored order; new entries
  append. An `order` field exists in the model for forward compatibility, but
  there is no reorder UI yet.
- Per-user / multi-tenant social lists — this is the single project's list.
- OAuth / live follower counts — pure static links.

## Storage (decision: A — dedicated JSON file)

Persist to `user_data_dir()/data/socials.json` via an atomic writer
(`tempfile.mkstemp` in the target's parent dir → `os.fdopen` write → `os.replace`,
mirroring `jarvis/ui/web/profile_routes.py` avatar write and `self_mod/writer.py`).

**Why not `jarvis.toml`:** the drift-guard daemon (BUG-010) watches `jarvis.toml`
and would fire on every social edit; social links are *content*, not *config*.
A dedicated JSON file satisfies the cloud-first €5-VPS doctrine just as well
(no Windows / GPU / native dependency) and keeps config clean. The store does
**not** depend on the Brain, so it works headless / with MockBrain (like the
avatar endpoints).

### File shape

```json
{
  "version": 1,
  "entries": [
    {
      "id": "b1c0…",            // uuid4 hex, server-assigned
      "platform": "discord",     // brand key (see brands.ts)
      "label": "Discord",        // display name, user-editable
      "url": "https://discord.gg/UPu6pFWrJ",
      "enabled": true,
      "order": 0
    }
  ]
}
```

## Backend — `jarvis/ui/web/socials_routes.py`

`APIRouter(prefix="/api/socials", tags=["socials"])`, included in
`server.py::_build_app()`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/socials` | list all entries (seed-on-first-run) |
| POST | `/api/socials` | add one (server assigns `id`, `order = max+1`) |
| PATCH | `/api/socials/{id}` | edit `platform` / `label` / `url` / `enabled` |
| DELETE | `/api/socials/{id}` | remove one (idempotent → 200) |

- Pydantic models: `SocialEntry` (response), `SocialCreate`, `SocialUpdate`
  (all fields optional for PATCH).
- URL validation: must start with `http://` or `https://` (reject `javascript:`
  and other schemes — anti-XSS, since the URL becomes an `href`). Length cap.
- Concurrency: a module-level lock around read-modify-write of the JSON file.
- Atomic write helper shared in the module (no `jarvis.toml`, no `config_writer`).

## Frontend — `jarvis/ui/web/frontend/src/views/socials/`

- `SocialsView.tsx` — header + "Add" button + card grid + empty state.
- `SocialCard.tsx` — brand logo, label, host, external-open anchor, edit/delete.
- `SocialEditDialog.tsx` — add/edit form (platform dropdown w/ real logos,
  label, url; validation mirrors backend).
- `BrandIcon.tsx` — renders official brand SVG (simple-icons path data) in the
  brand color; generic `Share2`/`Link` fallback for unknown platforms.
- `brands.ts` — `platform → { label, svgPath, hex }` map. Seeded brands:
  discord, github, x (twitter), youtube, instagram, linkedin, tiktok, website.
- `api.ts` (or React Query hooks) — typed CRUD client against `/api/socials`.

External links use `<a target="_blank" rel="noopener noreferrer">` — the
established pattern (PluginsView OAuth links); `window.open` is partly blocked
under pywebview.

## Section wiring (the 4 drift-prone sites)

1. `store/events.ts` — add `"socials"` to `SectionId` union, `SECTION_IDS`
   array, and `SECTION_LABELS`.
2. `components/layout/Sidebar.tsx` — append `{ id: "socials", labelKey:
   "nav.socials", icon: Share2 }` as the **last** `NAV_ITEMS` entry.
3. `components/layout/MainView.tsx` — `case "socials": return <SocialsView />`.
4. `i18n/locales/{de,en,es}.json` — add `nav.socials` (+ any `socials.*` view
   strings). i18n key + English source; UI source strings are never German.

Update `components/layout/Sidebar.test.tsx` for the new item count/label.

## Brand logos

Use the `original-logos` skill (official simple-icons SVG path data), inlined as
React components — no emoji, per the maintainer's "Original-Logos / no AI-slob"
mandate. No new npm dependency (path strings embedded in `brands.ts`).

## Testing

- **pytest** `tests/unit/ui/web/test_socials_routes.py` — CRUD, seed-on-first-run,
  atomic persistence (write survives, temp file cleaned), URL-scheme rejection,
  delete idempotency, headless (no-brain) operation. Use a temp data dir
  (monkeypatch `user_data_dir`).
- **vitest** `views/socials/SocialsView.test.tsx` — render list, empty state,
  add/edit/delete flow (mocked fetch), brand-icon fallback.
- **Self-verification:** run the app, screenshot the Socials section + add
  dialog, confirm Discord opens, attach to the report.

## Risks / notes

- Multi-layer enum drift (the 4 wiring sites) — TS compile catches a missed
  `SectionId`, but a missed `NAV_ITEMS`/`MainView` entry is silent. Build + a
  Sidebar test guard against it.
- `href` injection — backend URL-scheme allowlist + `rel="noopener noreferrer"`.
- Cloud-first — pure FastAPI + JSON file; boots on `python:3.11-slim`. No desktop
  extra touched.
