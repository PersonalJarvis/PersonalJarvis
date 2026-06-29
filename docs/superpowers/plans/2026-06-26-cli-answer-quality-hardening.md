# CLI Answer-Quality Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Jarvis answer naturally (not with raw JSON / table dumps) when the brain drives the `jarvisctl` control CLI, by emitting JSON to non-interactive consumers and teaching the brain how to read a `cli_*` tool result.

**Architecture:** Two small, independent changes. (1) `jarvis/cli_ctl/render.py::emit` emits JSON whenever stdout is not an interactive terminal (the brain runs `jarvisctl` with a piped stdout), keeping the Rich table only for a real human terminal. (2) The CONNECTED CLIS system-prompt section (`jarvis/clis/prompt_section.py`) gains a short, English, language-neutral rule describing the `cli_*` result shape and instructing the model to interpret `stdout` rather than read the envelope aloud.

**Tech Stack:** Python 3.11, Typer/Click CLI, Rich, pytest. Spec: `docs/superpowers/specs/2026-06-26-cli-answer-quality-hardening-design.md`.

**Interpreter note:** Run tests with the real CPython, not the uv shim: on this box that is `C:\Program Files\Python311\python.exe`. Commands below use `python -m pytest`; if `python` resolves to the uv shim, substitute the full path.

---

## File Structure

- Modify: `jarvis/cli_ctl/render.py` — add `_stdout_isatty()` helper; `emit()` chooses JSON when `as_json` OR not a TTY.
- Modify: `tests/unit/cli_ctl/test_render.py` — force TTY for the table test; add non-TTY→JSON, json-flag-wins, and isatty-fallback tests.
- Modify: `jarvis/clis/prompt_section.py` — extend `_FOOTER` with the `cli_*` result-interpretation rule.
- Modify: `tests/unit/clis/test_prompt_section.py` — add a test asserting the new interpretation rule is rendered.

No new files. Both tasks are independent and committed separately.

**Pre-flight (run once before Task 1):** confirm the editable install points at this working tree.

Run: `python -c "import jarvis; print(jarvis.__file__)"`
Expected: a path inside `<USER_HOME>\Desktop\Personal Jarvis\jarvis\__init__.py`. If it points elsewhere, run `pwsh scripts/preflight.ps1` and fix before proceeding (BUG-006/014).

---

## Task 1: JSON output when stdout is not a TTY

**Files:**
- Modify: `jarvis/cli_ctl/render.py:19-41` (the `emit` function; add helper above it)
- Test: `tests/unit/cli_ctl/test_render.py`

- [ ] **Step 1: Update the table test to force a TTY, and add the new behavior tests**

Replace the entire contents of `tests/unit/cli_ctl/test_render.py` with:

```python
# tests/unit/cli_ctl/test_render.py
import json

from jarvis.cli_ctl import render


def test_emit_json_mode_prints_raw_json(capsys):
    render.emit({"a": 1, "ä": "ö"}, as_json=True)  # i18n-allow: UTF-8 round-trip test data
    out = capsys.readouterr().out
    assert json.loads(out) == {"a": 1, "ä": "ö"}  # UTF-8 preserved, not escaped (i18n-allow)


def test_emit_human_list_of_dicts_prints_table(capsys, monkeypatch):
    # Force an interactive terminal so the human Rich-table path is exercised.
    monkeypatch.setattr(render, "_stdout_isatty", lambda: True)
    rows = [{"id": "1", "state": "scheduled"}, {"id": "2", "state": "running"}]
    render.emit(rows, as_json=False)
    out = capsys.readouterr().out
    assert "id" in out and "state" in out and "scheduled" in out


def test_emit_non_tty_defaults_to_json(capsys, monkeypatch):
    # The brain / pipes / scripts: stdout is not a TTY → JSON even without --json.
    monkeypatch.setattr(render, "_stdout_isatty", lambda: False)
    rows = [{"id": "1", "state": "scheduled"}]
    render.emit(rows, as_json=False)
    out = capsys.readouterr().out
    assert json.loads(out) == [{"id": "1", "state": "scheduled"}]


def test_emit_json_flag_wins_over_tty(capsys, monkeypatch):
    # An explicit --json forces JSON even in an interactive terminal.
    monkeypatch.setattr(render, "_stdout_isatty", lambda: True)
    render.emit({"k": "v"}, as_json=True)
    out = capsys.readouterr().out
    assert json.loads(out) == {"k": "v"}


def test_stdout_isatty_defaults_false_without_isatty(monkeypatch):
    # An exotic stdout wrapper without isatty must not crash; default to JSON.
    class _NoIsatty:
        pass

    monkeypatch.setattr(render.sys, "stdout", _NoIsatty())
    assert render._stdout_isatty() is False


def test_error_sets_message_on_stderr(capsys):
    render.error("boom")
    err = capsys.readouterr().err
    assert "boom" in err
```

- [ ] **Step 2: Run the tests to verify the new ones fail**

Run: `python -m pytest tests/unit/cli_ctl/test_render.py -v`
Expected: FAIL — `test_emit_non_tty_defaults_to_json` and `test_stdout_isatty_defaults_false_without_isatty` fail with `AttributeError: module 'jarvis.cli_ctl.render' has no attribute '_stdout_isatty'`; `test_emit_human_list_of_dicts_prints_table` also errors for the same reason (monkeypatch target missing).

- [ ] **Step 3: Implement the helper + non-TTY JSON default in `render.py`**

In `jarvis/cli_ctl/render.py`, add the helper immediately above `def emit(` (after the `_out`/`_err` Console definitions), and change the first line of `emit`'s body. The full updated region (from the helper through the end of `emit`) must read exactly:

```python
def _stdout_isatty() -> bool:
    """True only for a real interactive terminal. Any failure (an exotic stdio
    wrapper without ``isatty``, or one that raises) is treated as
    non-interactive, so non-TTY consumers — the brain's piped subprocess, a
    shell pipe, a script — receive machine-readable JSON."""
    try:
        return bool(sys.stdout.isatty())
    except Exception:  # noqa: BLE001 - non-interactive is the safe default
        return False


def emit(payload: Any, *, as_json: bool) -> None:
    # Machine-readable JSON when explicitly requested (--json) OR whenever stdout
    # is not an interactive terminal. The cli_jarvisctl tool runs `jarvisctl`
    # with a piped stdout, so the brain (and any pipe/script) gets parsable JSON
    # instead of a Rich table it would have to parse character-by-character.
    if as_json or not _stdout_isatty():
        # ensure_ascii=False keeps umlauts/emoji intact across platforms.
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return
    if isinstance(payload, list) and payload and all(
        isinstance(r, dict) for r in payload
    ):
        cols: list[str] = []
        for row in payload:
            for k in row:
                if k not in cols:
                    cols.append(k)
        table = Table(show_header=True, header_style="bold")
        for c in cols:
            table.add_column(str(c))
        for row in payload:
            table.add_row(*(str(row.get(c, "")) for c in cols))
        _out.print(table)
    elif isinstance(payload, (dict, list)):
        _out.print_json(json.dumps(payload, ensure_ascii=False))
    elif payload is not None:
        _out.print(str(payload))
```

(The body below the `if as_json or not _stdout_isatty():` guard is unchanged from the original — only the guard condition and the new helper + comment are added.)

- [ ] **Step 4: Run the render tests to verify they pass**

Run: `python -m pytest tests/unit/cli_ctl/test_render.py -v`
Expected: PASS — all six tests green.

- [ ] **Step 5: Run the full cli_ctl suite to confirm no regressions**

Run: `python -m pytest tests/unit/cli_ctl/ -q`
Expected: PASS. In particular `tests/unit/cli_ctl/test_safety.py::test_dry_run_prints_and_blocks` (asserts `"dry_run" in out or "POST" in out`) and `tests/unit/cli_ctl/test_dynamic_graft.py` (asserts `"pong" in res.output`) stay green — their substrings appear in the JSON output too.

- [ ] **Step 6: Lint**

Run: `ruff check jarvis/cli_ctl/render.py`
Expected: no errors.

- [ ] **Step 7: Commit (path-scoped — shared working tree)**

```bash
git add jarvis/cli_ctl/render.py tests/unit/cli_ctl/test_render.py
git commit -m "feat(cli): jarvisctl emits JSON to non-TTY consumers (brain/pipes/scripts)

render.emit now returns JSON whenever stdout is not an interactive terminal, so
the brain's piped subprocess gets parsable JSON instead of a Rich table. A real
human terminal still gets the table; an explicit --json still forces JSON.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `cli_*` result-interpretation rule in the system prompt

**Files:**
- Modify: `jarvis/clis/prompt_section.py:26-32` (the `_FOOTER` string)
- Test: `tests/unit/clis/test_prompt_section.py`

- [ ] **Step 1: Write the failing test**

Append this test to `tests/unit/clis/test_prompt_section.py`:

```python
def test_section_explains_cli_result_interpretation():
    from jarvis.clis.prompt_section import render_connected_clis_section

    fake = FakeCliRegistry({"gh": make_spec("gh")}, active=[FakeTool("cli_gh")])
    out = render_connected_clis_section(fake)
    # Describes the structured result shape …
    assert "exit_code" in out
    assert "stdout" in out
    # … and tells the model to interpret stdout, not read the envelope aloud.
    assert "summarize" in out.lower() or "natural" in out.lower()
    assert "never read" in out.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/clis/test_prompt_section.py::test_section_explains_cli_result_interpretation -v`
Expected: FAIL — `assert "exit_code" in out` fails (the footer does not mention it yet).

- [ ] **Step 3: Extend `_FOOTER` in `prompt_section.py`**

Replace the `_FOOTER` definition (`jarvis/clis/prompt_section.py:26-32`) with:

```python
_FOOTER = (
    "\nAnswer ONLY from the tool result — never invent external data. Prefer "
    "machine-readable output flags (--json, --format json) when the CLI "
    "supports them. If you are unsure of the exact command or flags, first run "
    "`<cli> --help` or `<cli> <group> --help` (read-only) to discover them, "
    "then issue the real command.\n"
    "Each cli_* tool result is a structured object: "
    "{success, output:{exit_code, stdout, stderr, duration_ms}, error}. "
    "An exit_code of 0 means success and stdout holds the real result (often "
    "JSON) — read it, then summarize it in your own natural words. Never read "
    "the result object, the JSON envelope, the exit code, or table characters "
    "aloud. On a non-zero exit_code, briefly explain the cause from stderr in "
    "plain language; never quote the error object."
)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/clis/test_prompt_section.py::test_section_explains_cli_result_interpretation -v`
Expected: PASS.

- [ ] **Step 5: Run the full prompt-section suite**

Run: `python -m pytest tests/unit/clis/test_prompt_section.py -v`
Expected: PASS — all existing tests (`test_renders_connected_cli_with_description_and_examples`, etc.) stay green; the new test passes.

- [ ] **Step 6: Lint**

Run: `ruff check jarvis/clis/prompt_section.py`
Expected: no errors.

- [ ] **Step 7: Commit (path-scoped)**

```bash
git add jarvis/clis/prompt_section.py tests/unit/clis/test_prompt_section.py
git commit -m "feat(cli): teach the brain to interpret cli_* results, not read them raw

Extend the CONNECTED CLIS prompt section with the cli_* result shape
(success/output{exit_code,stdout,stderr}/error) and the rule to summarize stdout
naturally instead of reading the JSON envelope or exit codes aloud.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final Verification

- [ ] **Step 1: Run both touched suites together**

Run: `python -m pytest tests/unit/cli_ctl/ tests/unit/clis/ -q`
Expected: PASS, no failures.

- [ ] **Step 2: Lint both changed modules**

Run: `ruff check jarvis/cli_ctl/render.py jarvis/clis/prompt_section.py`
Expected: no errors.

- [ ] **Step 3: Confirm the change is live for the running app (optional, manual)**

The editable install loads the new code, but the running FastAPI app + the
brain's already-built system prompt must rebuild. Restart via
`POST /api/settings/restart-app` (not `Stop-Process`). After restart, a brain
turn that calls `cli_jarvisctl` (e.g. "list my tasks", "switch the brain to
openai") should produce a natural spoken/written answer with no raw JSON or
table characters.

---

## Notes / Constraints

- **Language policy:** all added strings (prompt text, comments, commit messages) are English — required by the repo's CI `language-policy` gate. The prompt addition is product text the model reads, not a user-facing German string.
- **Latency:** Task 2 adds ~60 tokens to the system prompt only when at least one CLI is connected; no new LLM call. `scrub_for_voice` remains the regex safety net behind the model's answer.
- **Out of scope (tracked in the spec §7):** curated CLI groups for `settings`/`profile`/`self-mod`/`chats`, hardening `check_cli_coverage.py` to require a curated group per tag, and refining the `spawn_worker` Meta-Debug block.
```
