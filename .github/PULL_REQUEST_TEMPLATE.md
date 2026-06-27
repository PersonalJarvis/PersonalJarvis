<!--
Thanks for contributing to Personal Jarvis! Please fill out the sections below.
Keep all artifacts (code, comments, docs, this PR text) in English — see CONTRIBUTING.md.
-->

## What does this PR do?

<!-- A short, plain-language summary of the change and the motivation. -->

## Related issues

<!-- e.g. "Closes #123". Link any issue this addresses. -->

## Type of change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that changes existing behavior)
- [ ] Documentation only
- [ ] Refactor / internal cleanup (no behavior change)

## Checklist

- [ ] I read [`CONTRIBUTING.md`](../CONTRIBUTING.md) and, for larger changes, [`CLAUDE.md`](../CLAUDE.md) + [`docs/PHILOSOPHY.md`](../docs/PHILOSOPHY.md).
- [ ] All artifacts are in English (code, comments, docs, commit messages).
- [ ] Tests pass locally (`pytest -m "not slow"`), and I added tests for new behavior.
- [ ] New brain/STT/TTS/tool/channel providers pass the contract suite (`pytest tests/contract/`).
- [ ] No secrets, API keys, or personal data are included in the diff.
- [ ] Lint/format is clean (`ruff check jarvis/` && `ruff format jarvis/`).
- [ ] The change works on a headless Linux server (cloud-first), not only on a desktop — or local-only parts are gated behind an extras group with a graceful fallback.

## How was this tested?

<!-- Commands you ran, platforms you tested on (Linux / macOS / Windows), and what you observed. -->

## Screenshots / recordings (if UI-facing)

<!-- Drag in before/after images or a short clip for any visible change. -->
