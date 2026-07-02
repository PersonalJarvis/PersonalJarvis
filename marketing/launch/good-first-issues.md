# "good first issue" candidates

Ten candidates for the launch. Each is small, self-contained, does not require
understanding the whole architecture, and has an existing pattern to copy.
Before creating them on GitHub, give each a quick sanity check against current
`main` (file paths are indicative). Label: `good first issue`, plus the
suggested area label.

---

1. **Add a new STT provider behind the contract suite** (`area:speech`)
   The STT interface is a small streaming plugin (see
   `jarvis/plugins/stt/groq_api.py` as the reference). Pick a provider with a
   free tier (e.g., Deepgram), implement the plugin + entry-point, and make
   `pytest tests/contract/` pass. Great intro to the plugin system.

2. **Add a French locale** (`area:i18n`)
   The UI ships `en`/`de`/`es` locale JSONs under
   `jarvis/ui/web/frontend/src/i18n/locales/`. Add `fr.json` (translate from
   `en.json`), register it, and extend the runtime phrase tables so canned
   voice phrases resolve for `fr` too. The existing language parity tests show
   exactly what completeness means.

3. **Write a VPS deployment guide** (`area:docs`)
   A step-by-step `docs/` guide for a headless deploy on a generic Ubuntu VPS:
   install, `.env` key setup, `jarvis serve`, a systemd unit, and connecting
   the browser voice bridge. Everything needed already works — it just isn't
   written down as one walkthrough.

4. **Example tool plugin: weather via Open-Meteo** (`area:plugins`)
   A small reference `jarvis.tool` plugin calling the keyless Open-Meteo API,
   with a fake-based unit test. Doubles as living documentation for "how do I
   write a tool" — the most common contributor question.

5. **`jarvis --check` machine-readable output** (`area:cli`)
   The preflight check prints human text today. Add a `--json` flag emitting a
   stable schema (component, status, hint) so scripts and installers can gate
   on it. Small, well-bounded CLI change.

6. **Honor `prefers-reduced-motion` in the web UI** (`area:frontend`, `area:a11y`)
   The desktop/browser UI animates generously (orb, transitions, pulsing
   dots). Respect the OS-level reduced-motion setting via the standard media
   query and tone animations down to fades.

7. **Keyboard-shortcuts help overlay** (`area:frontend`)
   A `?`-triggered overlay listing the app's shortcuts. The frontend is
   React + Tailwind; the shortcut list already exists in code and just needs a
   presentation surface.

8. **Board stats CSV export** (`area:frontend`)
   The Board view aggregates activity stats. Add an "Export CSV" button that
   serializes the current aggregates client-side — no backend change needed.

9. **Wake-word sample recorder polish** (`area:speech`)
   The custom wake-word flow benefits from ~15 clean user recordings. Improve
   the recording helper UX: live level meter, clipping warning, and a summary
   of captured samples. Pure quality-of-life, no ML knowledge required.

10. **Installer smoke test in CI** (`area:install`)
    A GitHub Actions job that runs `install/install.sh --headless --no-launch
    --no-wizard` on a clean Ubuntu runner and asserts the venv boots and
    `jarvis --check` passes. Catches "works on the maintainer's machine"
    installer regressions — the project's most-feared bug class.
