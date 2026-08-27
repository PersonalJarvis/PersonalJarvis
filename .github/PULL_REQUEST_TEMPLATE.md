<!--
Thanks for contributing to Personal Jarvis.

Tick only the block that matches your change. A one-line docs fix does not owe
the same evidence as a new speech provider, and we do not pretend otherwise.
-->

## What does this PR do?

<!-- Two or three sentences: what changed, and why. Plain language is fine. -->

## Related issue

<!-- "Closes #123", or "none" if there isn't one. Small fixes don't need an issue first. -->

---

## Everyone

- [ ] **English only** — code, comments, docs, commit messages, and this PR text.
      Maintainers have to be able to read every line that lands; CI enforces it.
      Chat with us in any language you like on [Discord](https://discord.gg/x7USduHxbc).
- [ ] **One logical change.** Unrelated fixes go in their own PR.
- [ ] **No secrets, API keys, or personal data** anywhere in the diff.

## Then pick your block

<details open>
<summary><b>Docs, comments, or translations only</b></summary>

- [ ] Nothing else. Open it — this is the whole checklist.

</details>

<details>
<summary><b>Web UI / frontend change</b></summary>

- [ ] `npm run test` and `npm run build` pass in `jarvis/ui/web/frontend/`
- [ ] It works in **light and dark mode** — colours come from theme tokens, not one hardcoded mode
- [ ] Screenshot or clip below, before and after
- [ ] You did **not** commit `jarvis/ui/web/dist/` — the maintainer rebuilds the shipped bundle

</details>

<details>
<summary><b>Bug fix or ordinary Python change</b></summary>

- [ ] `pytest -m "not slow"` passes, and there is a test covering the fixed behaviour
- [ ] `ruff check jarvis/` and `ruff format --check jarvis/` are clean

</details>

<details>
<summary><b>New provider (wake / STT / TTS / brain / harness / tool / channel)</b></summary>

- [ ] `pytest tests/contract/` passes for the group you added to
- [ ] No `import jarvis.*` inside the plugin module; entry-point registered in `pyproject.toml`
- [ ] Secrets go through `get_secret()`; the provider degrades honestly without a key
- [ ] Any heavy or platform-specific dependency is an extra, not a base install

</details>

<details>
<summary><b>Change to a shared contract (capability, config schema, turn-taking, credentials, OS backend)</b></summary>

- [ ] Named which platforms and providers you touched, and what the others do now
      (unchanged / emulated / degraded, with the reason)
- [ ] `pytest tests/contract/` passes; a new gate has a test that fails without it
- [ ] It still imports and boots on a headless `python:3.11-slim` container
- [ ] `CHANGELOG.md` and the affected docs are updated

</details>

---

## How did you test it?

<!-- The commands you ran and the platform you ran them on (Linux / macOS / Windows).
     "pytest -m 'not slow' on Ubuntu 24.04" is a complete answer. -->

## Screenshots or recordings

<!-- Only for visible changes. Drag the files straight in. -->

<!--
First PR here? You do not need to get every box right. Open it, and we will
help you finish it in review. By contributing you agree your work is licensed
under the Apache License 2.0 — see docs/licensing.md.
-->
