"""Extract tool-call evidence + final answer from a claude stream.jsonl.

This is the keystone for making read-only / informational missions work:
the critic must SEE the tool calls + answer (today they get truncated out of
the 4000-char log summary), and the orchestrator must surface the worker's
answer to voice. Both consume `extract_stream_evidence`.
"""
from __future__ import annotations

import json

from jarvis.missions.stream_evidence import (
    extract_stream_evidence,
    extract_verified_commands,
    extract_verified_external_actions,
)


def _stream(lines: list[dict]) -> str:
    return "\n".join(json.dumps(line) for line in lines)


# ---------------------------------------------------------------------------
# extract_verified_commands — Git/GitHub side-effects that leave no worktree
# diff (commit/push/PR). The tool_result is the REAL subprocess output (the
# git/gh process wrote it), so a non-errored result is ground truth, not a log
# claim. This closes the "commit and push" / "open PRs" critic_loop_exhausted
# false-negative without weakening the anti-hallucination guard.
# ---------------------------------------------------------------------------


def test_credits_successful_git_push() -> None:
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "git push origin main"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "To github.com:me/repo.git\n   abc..def  main -> main"}]}},
    ])
    cmds = extract_verified_commands(stream)
    assert len(cmds) == 1
    assert "git push" in cmds[0][0]
    assert "main -> main" in cmds[0][1]


def test_credits_successful_structured_run_command() -> None:
    """API workers provide direct argv rather than a shell command string."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "RunCommand",
             "input": {"program": "git", "args": ["push", "origin", "main"]}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "abc..def  main -> main"}]}},
    ])
    commands = extract_verified_commands(stream)
    assert len(commands) == 1
    assert commands[0][0] == "git push origin main"


def test_structured_run_command_does_not_credit_mutating_words_in_echo_args() -> None:
    """A successful non-git executable cannot forge state-changing evidence."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "RunCommand",
             "input": {"program": "echo", "args": ["git", "push"]}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "git push"}]}},
    ])
    assert extract_verified_commands(stream) == ()


def test_structured_run_command_ignores_legacy_command_field() -> None:
    """Unused shell-string fields cannot override the argv that really ran."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "RunCommand",
             "input": {"program": "echo", "args": [],
                       "command": "git push origin main"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "ok"}]}},
    ])
    assert extract_verified_commands(stream) == ()


def test_credits_gh_pr_create() -> None:
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "p1", "name": "Bash",
             "input": {"command": "gh pr create --title 'Add X' --body '...'"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "p1",
             "content": "https://github.com/me/repo/pull/42"}]}},
    ])
    cmds = extract_verified_commands(stream)
    assert len(cmds) == 1
    assert "gh pr create" in cmds[0][0]
    assert "pull/42" in cmds[0][1]


def test_errored_command_not_credited() -> None:
    """A failed command (is_error result) is NOT credited — anti-hearsay."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "git push origin main"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
             "content": "error: failed to push some refs"}]}},
    ])
    assert extract_verified_commands(stream) == ()


def test_read_only_command_not_credited() -> None:
    """A read-only command (git status) is not a deliverable — not credited."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "git status"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "clean"}]}},
    ])
    assert extract_verified_commands(stream) == ()


def test_unmatched_command_result_not_credited() -> None:
    """A command whose result never arrived (truncated stream) is NOT credited."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "gh pr create --title x"}}]}},
    ])
    assert extract_verified_commands(stream) == ()


def test_credits_git_dash_C_push() -> None:
    """`git -C <dir> push` — a worker operating from a different cwd uses this
    constantly. The global `-C <path>` flag before the subcommand must not
    defeat crediting (code-review MAJOR-3)."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "git -C /home/me/repo push origin main"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "   abc..def  main -> main"}]}},
    ])
    assert len(extract_verified_commands(stream)) == 1


def test_credits_git_long_flag_before_subcommand() -> None:
    """`git --no-pager commit` — a long global flag before the subcommand."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "git --no-pager commit -m 'msg'"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "[main 1a2b3c4] msg\n 1 file changed"}]}},
    ])
    assert len(extract_verified_commands(stream)) == 1


def test_git_log_grep_push_not_a_false_positive() -> None:
    """`git log --grep=push` is read-only — the word 'push' inside a flag value
    must NOT be credited as a mutating command."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "git log --grep=push --oneline"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "abc commit"}]}},
    ])
    assert extract_verified_commands(stream) == ()


def test_chained_command_with_commit_and_push_is_credited() -> None:
    """A real-world chained command (add && commit && push) is credited once."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "c1", "name": "Bash",
             "input": {"command": "cd repo && git add -A && git commit -m 'x' && git push"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "c1",
             "content": "[main 1a2b3c4] x\n 1 file changed\n   ab..cd  main -> main"}]}},
    ])
    cmds = extract_verified_commands(stream)
    assert len(cmds) == 1
    assert "git commit" in cmds[0][0] or "git push" in cmds[0][0]


def test_extracts_tool_calls_and_final_answer() -> None:
    stream = _stream([
        {"type": "system", "subtype": "init", "tools": [], "mcp_servers": []},
        {"type": "system", "subtype": "init", "tools": [], "mcp_servers": []},  # hook noise
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Ich frage GitHub ab."},
            {"type": "tool_use", "name": "mcp__github__get_me", "input": {}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": [
                {"type": "text", "text": '{"login":"octocat"}'}]}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "mcp__github__search_repositories",
             "input": {"query": "user:octocat"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": [
                {"type": "text", "text": '{"total_count":32}'}]}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Du hast 32 aktive Repositories."}]}},
        {"type": "result",
         "result": "Du hast 32 aktive Repositories (1 öffentlich, 31 privat).",  # i18n-allow: simulated German voice-readback worker answer
         "subtype": "success", "is_error": False},
    ])
    ev = extract_stream_evidence(stream)

    assert "mcp__github__get_me" in ev.tool_calls
    assert "mcp__github__search_repositories" in ev.tool_calls
    assert ev.final_answer == "Du hast 32 aktive Repositories (1 öffentlich, 31 privat)."  # i18n-allow: simulated German voice-readback worker answer
    # at least one tool result captured (truncated form is fine)
    assert any("total_count" in r or "32" in r for r in ev.tool_results)


def test_final_answer_falls_back_to_last_assistant_text_without_result() -> None:
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"path": "x"}}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Die Datei erklärt das Modul X."}]}},  # i18n-allow: simulated German voice-readback worker answer
    ])
    ev = extract_stream_evidence(stream)
    assert ev.final_answer == "Die Datei erklärt das Modul X."  # i18n-allow: simulated German voice-readback worker answer
    assert "Read" in ev.tool_calls


def test_garbage_and_empty_lines_are_skipped() -> None:
    stream = "not json\n\n" + json.dumps(
        {"type": "result", "result": "ok", "subtype": "success"}
    )
    ev = extract_stream_evidence(stream)
    assert ev.final_answer == "ok"
    assert ev.tool_calls == ()


def test_no_tools_no_answer_is_empty() -> None:
    ev = extract_stream_evidence("")
    assert ev.tool_calls == ()
    assert ev.final_answer == ""
    assert not ev.has_tool_evidence


def test_verified_external_action_requires_correlated_success_result() -> None:
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "m1", "name": "mcp__gmail__send_message", "input": {}}
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "m1", "content": "message id 42"}
        ]}},
    ])

    assert extract_verified_external_actions(stream) == (
        ("mcp__gmail__send_message", "message id 42"),
    )


def test_verified_external_action_rejects_error_and_missing_result() -> None:
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "m1", "name": "mcp__gmail__send_message", "input": {}},
            {"type": "tool_use", "id": "m2", "name": "mcp__linear__create_issue", "input": {}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "m1", "content": "denied", "is_error": True}
        ]}},
    ])

    assert extract_verified_external_actions(stream) == ()


def test_codex_mcp_completion_normalizes_to_verified_external_action() -> None:
    stream = _stream([
        {"type": "item.completed", "item": {
            "type": "mcp_tool_call",
            "server": "gmail",
            "tool": "send_message",
            "status": "completed",
            "result": "message id 84",
        }}
    ])

    assert extract_verified_external_actions(stream) == (
        ("mcp__gmail__send_message", "message id 84"),
    )


# --- readonly_answer: the read-only success signal -------------------------
from jarvis.missions.stream_evidence import readonly_answer, summarize_answers  # noqa: E402

_GH_STREAM = _stream([
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "mcp__github__search_repositories", "input": {}}]}},
    {"type": "result", "result": "Du hast 32 aktive Repositories.", "subtype": "success"},
])


def test_readonly_answer_returns_answer_on_empty_diff_with_tool_evidence() -> None:
    assert readonly_answer("", _GH_STREAM) == "Du hast 32 aktive Repositories."
    assert readonly_answer("   \n  ", _GH_STREAM) == "Du hast 32 aktive Repositories."


def test_readonly_answer_none_when_diff_present() -> None:
    # a code task that produced a diff is NOT an informational result
    assert readonly_answer("diff --git a/x b/x\n+hello", _GH_STREAM) is None


def test_readonly_answer_none_without_tool_evidence() -> None:
    # anti-hallucination: empty diff + NO tool calls -> not a success
    bare = _stream([{"type": "result", "result": "Habe alles erledigt.", "subtype": "success"}])
    assert readonly_answer("", bare) is None


def test_readonly_answer_none_when_answer_empty() -> None:
    no_answer = _stream([{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {}}]}}])
    assert readonly_answer("", no_answer) is None


def test_summarize_answers_joins_and_caps() -> None:
    assert summarize_answers([]) == ""
    assert summarize_answers(["A", "B"]) == "A\nB"
    long = "x" * 5000
    assert len(summarize_answers([long], cap=600)) <= 600


def test_summarize_answers_truncates_on_sentence_boundary_not_mid_word() -> None:
    """A spoken readback that overflows the cap must end on a complete word /
    sentence, never mid-word. Live forensic 2026-06-19 (session 514cddc0): the
    hard ``[:cap-1]`` cut produced "…eine schlechtere Auswander…" — the TTS
    spoke a fragment and stopped mid-word, which the user heard as Jarvis
    "hanging up mid-sentence"."""
    text = ("Emigrating abroad is a serious decision. " * 40).strip()
    assert len(text) > 600
    out = summarize_answers([text], cap=600)

    assert len(out) <= 600
    assert out.endswith("…")
    core = out[:-1].rstrip()
    # Only a suffix was removed — no word was sliced in half.
    assert text.startswith(core)
    after = text[len(core) : len(core) + 1]
    assert after in ("", " ", "\n") or core[-1] in ".!?…"


def test_summarize_answers_word_boundary_without_punctuation() -> None:
    """Even when the overflowing text has no sentence punctuation in range, the
    cut falls back to the last word boundary — never a partial token."""
    text = "relocation " * 100  # 1100 chars, no sentence enders; cap lands mid-word
    out = summarize_answers([text], cap=600)

    assert len(out) <= 600
    assert out.endswith("…")
    core = out[:-1].rstrip()
    assert set(core.split()) == {"relocation"}


# --- is_informational_request + conversational (no-tool) answer ------------
#
# Live mission 019ec638 (2026-06-14): "which city would you recommend for a
# trip to Australia?" was dispatched as a heavy mission. The worker answered
# correctly in text but wrote no file, so the empty-diff veto rejected it 3×
# and the mission FAILED (critic_loop_exhausted). A pure question's deliverable
# IS the spoken answer — but the relaxation must key off the REQUEST shape,
# never the worker's claim, so the anti-hallucination veto still fires for a
# do-task that produced nothing.
from jarvis.missions.stream_evidence import (  # noqa: E402
    informational_file_answer,
    is_clarification_only_answer,
    is_informational_request,
)

# The standing quality directive spawn_worker prepends to EVERY mission prompt
# (_QUALITY_DIRECTIVE) — the classifier must look past it at the real request.
_DIRECTIVE = (
    "Deliver a complete, polished, production-quality result that fully "
    'satisfies the request. A skeleton, stub, placeholder, or "content '
    'follows" / "Inhalt folgt" shell is a FAILURE — never ship one. If a '
    "detail is unspecified, build the finished artefact."
)


def test_is_informational_request_true_for_questions() -> None:
    assert is_informational_request(
        "Could you please tell me which city you would recommend if I would "
        "like to book a trip to Australia?"
    )
    assert is_informational_request("Was hältst du von exp.com?")  # i18n-allow: simulated German user question, bilingual classifier coverage
    assert is_informational_request("Explain how the event bus works.")
    assert is_informational_request("Welche Stadt empfiehlst du für eine Reise?")  # i18n-allow: simulated German user question, bilingual classifier coverage
    # behind the standing quality directive (the real dispatched shape)
    assert is_informational_request(
        f"{_DIRECTIVE}\n\nWhich city would you recommend for a trip to Australia?"
    )


def test_is_informational_request_false_for_do_tasks() -> None:
    assert not is_informational_request(
        "Write a 180-word founding myth into a file named stonehollow.txt"
    )
    assert not is_informational_request("Erstelle eine HTML-Seite namens index.html")
    assert not is_informational_request("Build a Flask app on port 8000")
    # an action task wearing a question mark must NOT pass — the action verb wins
    assert not is_informational_request("Can you create a file report.md?")
    assert not is_informational_request(
        f"{_DIRECTIVE}\n\nCan you open Chrome and go to example.com?"
    )


def test_is_informational_request_false_for_noun_phrase_do_tasks() -> None:
    """Adversarial (code review 2026-06-14): a do-task phrased as a noun phrase
    with a trailing '?' must NOT slip past the veto — otherwise its no-file text
    answer would be wrongly approved. The deliverable here is an artefact, not an
    answer. Caught by the action/artefact verb list."""
    assert not is_informational_request("A PDF export of the Q1 spreadsheet?")
    assert not is_informational_request("The conversion of the Word document to HTML?")
    assert not is_informational_request("A ZIP archive of the project files?")
    assert not is_informational_request("Rendering of the report to PDF?")
    assert not is_informational_request("A summary table, exported to data.csv?")


def test_is_informational_request_true_for_advisory_imperatives() -> None:
    """Doable advisory / planning imperatives have NO file deliverable — the plan
    or answer IS the result. Live mission 019ec708 (2026-06-14): "plan a trip"
    failed because the old rule required an interrogative lead word it lacks. The
    rule covers questions AND advisory triggers (plan/suggest/research/…) alike."""
    assert is_informational_request("plan a trip from London to Taiwan")
    assert is_informational_request("Suggest three restaurants near the Louvre.")
    assert is_informational_request("Research the best laptops under 1000 euros.")
    assert is_informational_request("Give me a weekend itinerary for Lisbon.")
    # the exact live shape (meta-phrase + standing quality directive)
    assert is_informational_request(
        f"{_DIRECTIVE}\n\nI would like you to spawn a sub-agent which will help "
        "me plan a trip from London to Taiwan."
    )


def test_is_informational_request_false_for_do_and_transaction_tasks() -> None:
    """Two classes stay NON-informational: (a) file/code/side-effect do-tasks,
    and (b) impossible real-world TRANSACTIONS ("book me a trip", "buy me X").
    Transactions lack any informational trigger, so they fall through to the
    capability-refusal path (one-shot honest reject) rather than a wrongful
    approve — and "book" the verb never collides with "book" the noun."""
    # file/code do-tasks (no trigger and/or an action verb)
    assert not is_informational_request("Refactor the auth module.")
    assert not is_informational_request("Configure nginx as a reverse proxy.")
    assert not is_informational_request("Rename all the test files.")
    assert not is_informational_request("Send an email to the team.")
    # impossible transactions — handled by capability-refusal, NOT informational
    assert not is_informational_request("Please book me a trip from Melbourne to Tokyo.")
    assert not is_informational_request("Buy me a flight to Tokyo for under 800 euros.")
    # but "book" the NOUN inside a real advisory request stays informational
    assert is_informational_request("Recommend a good book about the Roman Empire.")


def test_is_informational_request_true_despite_start_mission_meta() -> None:
    """Routing meta-language ("start a Sub-Edge-Mission that …", "start a worker
    that …") must NOT mask a genuine research request. Live mission 019ecb56
    (2026-06-15): a German "please start a Sub-Edge-Mission in which you research
    the current AI news" was mis-classified as a do-task because the launch verb
    (German ``starten``) lives in the action-verb list — the mission then ran the
    adversarial CODE-critic on a research report and died critic_loop_exhausted.
    The spawn/launch verb paired with an agent/mission noun is routing meta, not
    an artefact verb; strip it before classifying. The German fixtures below
    reproduce the exact live shape."""
    # simulated German user utterances (test fixtures) — i18n-allow per line below
    assert is_informational_request(
        "Kannst du mir bitte eine Sub-Edge-Mission starten, in der du "  # i18n-allow
        "recherchierst, was die aktuellen AI-News sind von den letzten Jahren?"  # i18n-allow
    )
    # the exact dispatched shape (standing quality directive + meta + Aufgabe)
    assert is_informational_request(
        f"{_DIRECTIVE}\n\nAufgabe: eine umfassende Recherche zu den KI-News der "  # i18n-allow
        "letzten Jahre durchführt.\nWortlaut des Nutzers: \"Kannst du mir bitte "  # i18n-allow
        "eine Sub-Edge-Mission starten, in der du recherchierst, was die "  # i18n-allow
        "aktuellen AI-News sind?\"."  # i18n-allow
    )
    # English launch-verb + agent noun, same shape
    assert is_informational_request(
        "Start a sub-agent to research the best laptops under 1000 euros."
    )


def test_is_informational_request_true_for_make_me_deep_research_phrase() -> None:
    """Live mission 019ecbb8 used "make me a deep research" to mean a research
    brief, not a file/code side effect. The generic "make" action verb must not
    mask the surrounding informational research request."""
    assert is_informational_request(
        "I would like you to help me research about the topic with the "
        "sub-agent and the topic is how I can move to the USA from Germany "
        "and how my chances are realistically if you compare it with "
        "countries like Mexico or something like that and make me a deep "
        "research with the sub-agent."
    )


def test_start_mission_meta_strip_does_not_unmask_real_do_tasks() -> None:
    """Stripping the spawn-meta clause must leave the REAL task verb intact, so a
    do-task wrapped in routing meta stays a do-task (anti-hallucination guard)."""
    # the real verb (schreibt/creates a named file) survives the strip
    assert not is_informational_request(
        "Starte einen Sub-Agenten, der eine Datei report.md schreibt."  # i18n-allow
    )
    assert not is_informational_request(
        "Start a worker that creates an index.html landing page."
    )
    assert not is_informational_request(
        "Make me a research script in Python that scrapes example.com."
    )


def test_is_informational_request_true_for_create_spawn_subagent_research() -> None:
    """Live regression (2026-06-16, "move to the USA" mission): the explicit
    voice phrasing "Create and spawn a sub-agent which will help me find out X"
    was mis-classified as a do-task because the leading creation verb "Create"
    survived the spawn-meta strip and tripped the action-verb veto. A creation
    verb governing a routing noun ("create a sub-agent", "create and spawn a
    sub-agent") is routing meta, not an artefact verb — strip it so the real
    research request ("find out what I have to be aware of …") is seen."""
    assert is_informational_request(
        "Create and spawn a sub-agent which will help me find out what I have "
        "to be aware of when I move to the USA."
    )
    # behind the standing quality directive (the real dispatched shape)
    assert is_informational_request(
        f"{_DIRECTIVE}\n\nCreate and spawn a sub-agent which will help me find "
        "out what I have to be aware of when I move to the USA."
    )
    # the direct form ("create a sub-agent to research …")
    assert is_informational_request(
        "Create a sub-agent to research the best laptops under 1000 euros."
    )
    # German inflected routing noun
    assert is_informational_request(
        "Spawne einen Sub-Agenten, der herausfindet, was ich beim "  # i18n-allow
        "USA-Umzug beachten muss."  # i18n-allow
    )


def test_create_spawn_meta_strip_keeps_real_do_tasks_noninformational() -> None:
    """The creation-verb strip is restricted to Jarvis routing nouns
    (sub-agent/worker/mission), so a genuine deliverable governed by a creation
    verb ("create a file", "build an app/framework") is NEVER unmasked into an
    informational request — the anti-hallucination veto stays intact."""
    assert not is_informational_request("Create a file for the agent's config.")
    assert not is_informational_request("Build an agent framework in Python.")
    assert not is_informational_request("Write a report about agents.")
    assert not is_informational_request(
        "Erstelle eine Datei und teste sie."  # i18n-allow
    )
    # a deliverable wrapped in a spawn trigger keeps its real verb/file
    assert not is_informational_request(
        "Create a sub-agent that writes a file report.md."
    )


def test_readonly_answer_accepts_no_tool_answer_for_informational_prompt() -> None:
    # pure Q&A: no tool calls, no diff — the spoken answer IS the deliverable.
    convo = _stream([
        {"type": "result",
         "result": "I'd recommend Sydney as your first stop.",
         "subtype": "success"},
    ])
    prompt = "Which city would you recommend for a trip to Australia?"
    assert (
        readonly_answer("", convo, prompt=prompt)
        == "I'd recommend Sydney as your first stop."
    )
    # without the prompt the anti-hallucination guard still returns None
    assert readonly_answer("", convo) is None


def test_readonly_answer_rejects_no_tool_answer_for_do_task_prompt() -> None:
    # a do-task that produced no tools/diff but claims success: still vetoed,
    # even with the prompt — the request is not informational.
    convo = _stream([
        {"type": "result", "result": "I have created the file.", "subtype": "success"},
    ])
    prompt = "Create a file report.md with the analysis."
    assert readonly_answer("", convo, prompt=prompt) is None


def test_readonly_answer_rejects_worker_clarification_as_a_result() -> None:
    """Exact 2026-07-13 failure: a long format question was auto-approved as
    the completed research answer merely because the request was informational."""
    clarification = (
        "# Drugs in schools\n\n"
        "Kurze Rückfrage, weil aus deiner Nachricht "  # i18n-allow: fixture
        "ein Wort fehlt — einen was genau? "  # i18n-allow: fixture
        "Soll ich einen Vortrag, einen Aufsatz, "  # i18n-allow: fixture
        "eine Präsentation oder ein Infoblatt erstellen?"  # i18n-allow: fixture
    )
    convo = _stream([
        {"type": "result", "result": clarification, "subtype": "success"},
    ])

    assert is_clarification_only_answer(clarification)
    assert readonly_answer(
        "",
        convo,
        prompt="Research and analyze drugs in schools.",
    ) is None


# --- informational_file_answer: research request answered in a prose document --
#
# Root cause of mission_019ecb56 (2026-06-15): "recherchiere AI-News" is an
# informational request, but the worker reasonably wrote the answer into a
# Markdown REPORT (KI-News-Bericht.md). A non-empty diff routed it to the
# adversarial CODE-critic, which demanded reachable web citations a web-less
# worker cannot produce → 3× revise → critic_loop_exhausted. When an
# informational request's whole deliverable is substantive PROSE (.md/.txt/…),
# the document IS the answer — grade it as prose, never as code.


def _prose(body: str, path: str = "KI-News-Bericht.md") -> str:
    plus = "\n".join("+" + ln for ln in body.splitlines())
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "index 0000000..4957e1f\n"
        f"--- /dev/null\n+++ b/{path}\n"
        f"@@ -0,0 +1,{len(body.splitlines())} @@\n{plus}\n"
    )


_RESEARCH_PROMPT = (
    "Start a sub-agent to research the recent AI news of the last few years."
)
_REPORT_BODY = (
    "# Recent AI news of the last few years\n\n"
    "AI development over the last few years shows a clear acceleration on "
    "several fronts: larger language models, multimodal systems, and the jump "
    "from chatbots to agentic workflows have dominated the headlines. This "
    "report summarises the most important breakthroughs, trends, and their "
    "context in a structured form, from the foundation models to the most "
    "recent regulatory debates."
)


def test_informational_file_answer_returns_prose_for_research_request() -> None:
    answer = informational_file_answer(_prose(_REPORT_BODY), prompt=_RESEARCH_PROMPT)
    assert answer is not None
    assert "Recent AI news" in answer


def test_informational_file_answer_none_for_code_diff() -> None:
    # a real code change is NOT a prose deliverable — keep the code-critic.
    code = _prose("def main():\n    return 42\n", path="app.py")
    assert informational_file_answer(code, prompt=_RESEARCH_PROMPT) is None


def test_informational_file_answer_none_for_do_task_prompt() -> None:
    # the request named a file / used an artefact verb → not informational.
    do_prompt = "Create a file report.md summarising the AI news."
    assert informational_file_answer(_prose(_REPORT_BODY), prompt=do_prompt) is None


def test_informational_file_answer_none_for_stub_document() -> None:
    # a near-empty / stub prose file is not a real answer — let the critic see it.
    stub = _prose("# KI-News\n\nInhalt folgt.\n")
    assert informational_file_answer(stub, prompt=_RESEARCH_PROMPT) is None


def test_informational_file_answer_none_for_long_clarification() -> None:
    clarification = (
        "# Requested research\n\n"
        "Quick clarifying question: which format should I use? "
        "I can prepare a presentation, essay, handout, or detailed report. "
        "Each option can include an introduction, a structured main section, "
        "conclusions, prevention guidance, and references. Please choose the "
        "format and audience before I begin so that I can tailor the result."
    )

    assert len(clarification) >= 300
    assert informational_file_answer(
        _prose(clarification), prompt=_RESEARCH_PROMPT
    ) is None


def test_informational_file_answer_none_when_code_mixed_in() -> None:
    # research request, but the diff also touches a code file → conservative:
    # fall through to the code-critic (do not blanket-approve).
    mixed = _prose(_REPORT_BODY) + _prose("print('x')\n", path="gen.py")
    assert informational_file_answer(mixed, prompt=_RESEARCH_PROMPT) is None


# --- extract_write_targets: out-of-worktree deliverable verification --------
#
# Root cause of mission_019e7abd (2026-05-30): the worker wrote a file to an
# absolute path OUTSIDE its git worktree (the user's Desktop\M\). `_capture_diff`
# is worktree-scoped → empty diff → the Critic's GROUND-TRUTH-RULE failed it 3×
# even though the file existed and was correct. `extract_write_targets` is the
# stream-side half of the fix: surface the paths the worker actually wrote with
# a real, non-errored Write/Edit tool_use so the Kontrollierer can verify them
# on disk and present them to the Critic as ground truth.
from jarvis.missions.stream_evidence import extract_write_targets  # noqa: E402


def test_extract_write_targets_returns_successful_write_path() -> None:
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu1", "name": "Write",
             "input": {"file_path": r"C:\Users\x\M\hello.html",
                       "content": "<h1>Hi</h1>"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tu1",
             "content": [{"type": "text", "text": "File created successfully."}]}]}},
        {"type": "result", "result": "done", "subtype": "success"},
    ])
    assert extract_write_targets(stream) == (r"C:\Users\x\M\hello.html",)


def test_extract_write_targets_excludes_errored_write() -> None:
    # iter1 of the live failure: Write returned `File has not been read yet`.
    # An errored-only path must NOT be credited — the worker did not write it.
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu1", "name": "Write",
             "input": {"file_path": r"C:\x\f.txt", "content": "x"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tu1", "is_error": True,
             "content": [{"type": "text",
                          "text": "<tool_use_error>File has not been read yet."
                                  "</tool_use_error>"}]}]}},
    ])
    assert extract_write_targets(stream) == ()


def test_extract_write_targets_edit_tool_counts() -> None:
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "e1", "name": "Edit",
             "input": {"file_path": "/tmp/out/report.md",
                       "old_string": "a", "new_string": "b"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "e1",
             "content": [{"type": "text", "text": "Edit applied."}]}]}},
    ])
    assert extract_write_targets(stream) == ("/tmp/out/report.md",)


def test_extract_write_targets_dedups_path_with_one_success() -> None:
    # Same path written twice: first errored, then succeeded → credited once.
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Write",
             "input": {"file_path": "/tmp/x.txt", "content": "1"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a", "is_error": True,
             "content": [{"type": "text", "text": "<tool_use_error>nope</tool_use_error>"}]}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "b", "name": "Write",
             "input": {"file_path": "/tmp/x.txt", "content": "2"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "b",
             "content": [{"type": "text", "text": "ok"}]}]}},
    ])
    assert extract_write_targets(stream) == ("/tmp/x.txt",)


def test_extract_write_targets_empty_without_write_tools() -> None:
    # Read-only / informational stream (only a search tool) → no write targets.
    assert extract_write_targets(_GH_STREAM) == ()
    assert extract_write_targets("") == ()


def test_extract_write_targets_excludes_idless_write_frame() -> None:
    """A tool_use with no `id` cannot be correlated to a result → it must NOT
    be credited. Closes the hearsay hole for out-of-worktree writes: a malformed
    frame must not let a pre-existing file masquerade as freshly written."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Write",
             "input": {"file_path": "/tmp/no_id.txt", "content": "x"}}]}},
    ])
    assert extract_write_targets(stream) == ()


def test_extract_write_targets_excludes_unmatched_write_frame() -> None:
    """A write frame whose tool_result never arrived (truncated stream) is not
    a confirmed success → not credited. Only a matched, non-errored result
    counts as ground truth."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "w1", "name": "Write",
             "input": {"file_path": "/tmp/truncated.txt", "content": "x"}}]}},
        # no tool_result for w1
    ])
    assert extract_write_targets(stream) == ()


def test_readonly_answer_returns_answer_for_external_only_diff() -> None:
    """An external-target-only diff (out-of-worktree deliverable) carries no
    in-worktree `diff --git` hunk, so the worker's final answer — which names
    the delivered file — must still be spoken back, not suppressed as a 'code
    task'. Regression for the mission_019e7abd fix: after the external-write
    augmentation the diff is non-empty, and a naive truthiness gate would
    silence the readback."""
    external_diff = (
        "diff --external-target b/C:/Users/x/M/hello.html\n"
        "# verified-external-write: C:/Users/x/M/hello.html\n"
        "--- /dev/null\n+++ b/C:/Users/x/M/hello.html\n+<h1>Hi</h1>\n"
    )
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "w1", "name": "Write",
             "input": {"file_path": "C:/Users/x/M/hello.html",
                       "content": "<h1>Hi</h1>"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "w1",
             "content": [{"type": "text", "text": "ok"}]}]}},
        {"type": "result",
         "result": "Created hello.html at C:/Users/x/M/hello.html.",
         "subtype": "success"},
    ])
    assert (
        readonly_answer(external_diff, stream)
        == "Created hello.html at C:/Users/x/M/hello.html."
    )


# ---------------------------------------------------------------------------
# extract_verified_desktop_actions — desktop-launch commands that produce NO
# file diff: the deliverable is a running process, not a file change.
# Mirrors the git/gh command-evidence path so a diff-less "open Explorer /
# launch Chrome" mission can be credited as real work instead of being vetoed
# as an empty diff. Anti-hearsay discipline: SAME rules as
# extract_verified_commands (id required; non-errored result required).
# ---------------------------------------------------------------------------
from jarvis.missions.stream_evidence import extract_verified_desktop_actions  # noqa: E402


def test_extract_verified_desktop_actions_credits_silent_launch() -> None:
    """Windows 'start explorer.exe' with an empty (but non-errored) result is
    credited — a silent detached spawn produces no stdout, and that is SUCCESS,
    not missing evidence."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "d1", "name": "Bash",
             "input": {"command": "start explorer.exe"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "d1",
             "content": ""}]}},
    ])
    actions = extract_verified_desktop_actions(stream)
    assert len(actions) == 1
    assert "start explorer.exe" in actions[0][0]
    # Silent detached spawn: empty stdout → substitute sentinel text.
    assert actions[0][1] == "(command succeeded; no output captured)"


def test_extract_verified_desktop_actions_skips_errored() -> None:
    """A launch command whose tool_result is errored is NOT credited — the
    process did not start (or the shell reported failure)."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "d2", "name": "Bash",
             "input": {"command": "start explorer.exe"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "d2", "is_error": True,
             "content": "The system cannot find the file specified."}]}},
    ])
    assert extract_verified_desktop_actions(stream) == ()


def test_extract_verified_desktop_actions_ignores_readonly() -> None:
    """Read-only commands (ls, cat) do NOT match the desktop-launch regex and
    must never be credited (false-positive guard)."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "r1", "name": "Bash",
             "input": {"command": "ls -la"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "r1",
             "content": "total 0"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "r2", "name": "Bash",
             "input": {"command": "cat foo.txt"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "r2",
             "content": "hello"}]}},
    ])
    assert extract_verified_desktop_actions(stream) == ()


def test_extract_verified_desktop_actions_linux_xdg_open() -> None:
    """Linux: 'xdg-open foo.pdf' is a desktop-launch command and is credited."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "x1", "name": "Bash",
             "input": {"command": "xdg-open /home/user/document.pdf"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "x1",
             "content": ""}]}},
    ])
    actions = extract_verified_desktop_actions(stream)
    assert len(actions) == 1
    assert "xdg-open" in actions[0][0]
    assert actions[0][1] == "(command succeeded; no output captured)"


def test_structured_run_command_credits_real_xdg_open_program() -> None:
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "x2", "name": "RunCommand",
             "input": {"program": "xdg-open", "args": ["artifact.pdf"]}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "x2", "content": ""}]}},
    ])
    actions = extract_verified_desktop_actions(stream)
    assert len(actions) == 1
    assert actions[0][0] == "xdg-open artifact.pdf"


def test_structured_run_command_does_not_credit_launcher_words_in_echo_args() -> None:
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "x3", "name": "RunCommand",
             "input": {"program": "echo", "args": ["xdg-open", "artifact.pdf"]}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "x3", "content": "ok"}]}},
    ])
    assert extract_verified_desktop_actions(stream) == ()


def test_extract_verified_desktop_actions_macos_open() -> None:
    """macOS: 'open -a Safari' is a desktop-launch command and is credited."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "m1", "name": "Bash",
             "input": {"command": "open -a Safari https://example.com"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "m1",
             "content": ""}]}},
    ])
    actions = extract_verified_desktop_actions(stream)
    assert len(actions) == 1
    assert "open -a" in actions[0][0]
    assert actions[0][1] == "(command succeeded; no output captured)"


def test_extract_verified_desktop_actions_skips_uncorrelated() -> None:
    """A tool_use with no id cannot be correlated to a result and must NOT be
    credited — anti-hearsay gate mirrors extract_verified_commands."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            # No "id" field — cannot correlate to a result.
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "start explorer.exe"}}]}},
    ])
    assert extract_verified_desktop_actions(stream) == ()


def test_extract_verified_desktop_actions_skips_unmatched_result() -> None:
    """A tool_use whose result never arrived (truncated stream) is NOT credited."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t99", "name": "Bash",
             "input": {"command": "start explorer.exe"}}]}},
        # no tool_result for t99
    ])
    assert extract_verified_desktop_actions(stream) == ()


# ---------------------------------------------------------------------------
# MAJOR-2 — tightened `start` arm: CLI runs must NOT be credited as desktop
# launches, but real GUI/app launches must still be credited.
# ---------------------------------------------------------------------------


def _desktop_stream(command: str) -> str:
    """Build a minimal successful shell tool_use stream for `command`."""
    return _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "s1", "name": "Bash",
             "input": {"command": command}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "s1", "content": ""}]}},
    ])


def test_start_explorer_still_credited_after_tightening() -> None:
    """Real GUI launch: 'start explorer.exe' must still be credited."""
    assert len(extract_verified_desktop_actions(_desktop_stream("start explorer.exe"))) == 1


def test_start_chrome_still_credited_after_tightening() -> None:
    """Real GUI launch: 'start chrome' must still be credited."""
    assert len(extract_verified_desktop_actions(_desktop_stream("start chrome"))) == 1


def test_start_calc_still_credited_after_tightening() -> None:
    """Real GUI launch: 'start calc' must still be credited."""
    assert len(extract_verified_desktop_actions(_desktop_stream("start calc"))) == 1


def test_start_quoted_title_chrome_still_credited() -> None:
    """Real GUI launch: 'start \"\" chrome' (quoted-title form) must still be credited."""
    assert len(extract_verified_desktop_actions(_desktop_stream('start "" chrome'))) == 1


def test_start_git_push_not_credited() -> None:
    """MAJOR-2: 'start git push' is a CLI run, NOT a desktop launch — must not be credited."""
    assert extract_verified_desktop_actions(_desktop_stream("start git push")) == ()


def test_start_python_build_not_credited() -> None:
    """MAJOR-2: 'start python build.py' is a CLI run, NOT a desktop launch — must not be credited."""
    assert extract_verified_desktop_actions(_desktop_stream("start python build.py")) == ()


def test_start_slash_B_not_credited() -> None:
    """MAJOR-2: 'start /B git status' uses a Windows flag — NOT a named-app GUI launch."""
    assert extract_verified_desktop_actions(_desktop_stream("start /B git status")) == ()


def test_start_npm_run_build_not_credited() -> None:
    """MAJOR-2: 'start npm run build' is a CLI tool run — must not be credited as a launch."""
    assert extract_verified_desktop_actions(_desktop_stream("start npm run build")) == ()
