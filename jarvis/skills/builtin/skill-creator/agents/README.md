# agents/

Sub-agent personas as Markdown files. Each file describes a role
(e.g. Grader, Analyzer, Comparator) that the Supervisor can inject into the
system prompt at dispatch time.

**Jarvis adaptation note:** Anthropic's original ships ``analyzer.md``,
``comparator.md``, ``grader.md`` here for their eval framework. Without an eval
framework, those are not immediately useful. The folder is kept empty as a placeholder.

**Example Jarvis usage:** If you use the ``dispatch_to_harness`` flow and
need a sub-Claude with a specific persona, you can drop e.g.
``code-reviewer.md`` here and the skill runner prepends its content as a
prompt prefix before the user turn.
