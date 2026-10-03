import type { IdeSkillDraft } from "@/lib/ideSkillsApi";

/**
 * Three example skills an empty library offers to add in one click, so the
 * first drag onto a terminal can happen before the user has written anything.
 * Plain, general-purpose instructions that work with any coding agent.
 */
export const STARTER_SKILLS: readonly IdeSkillDraft[] = [
  {
    title: "Plan before coding",
    hue: "violet",
    icon: "brain",
    content: `# Plan before coding

Before you change any file:

1. Restate the goal in one sentence.
2. List the files you expect to touch and why.
3. Name the riskiest part of the change and how you will check it.
4. Wait for my go-ahead, then implement in small steps.

Keep the plan short — ten lines at most.
`,
  },
  {
    title: "Test-first bug fix",
    hue: "rose",
    icon: "bug",
    content: `# Test-first bug fix

Fix the bug described below with this order of work:

- Write a failing test that reproduces the bug. Run it and show me it fails.
- Find the root cause. Explain it in two or three sentences.
- Make the smallest change that turns the test green.
- Run the surrounding tests and report anything that broke.

Do not refactor unrelated code in the same change.

Bug:
`,
  },
  {
    title: "Code review checklist",
    hue: "amber",
    icon: "shield",
    content: `# Code review checklist

Review the current diff. For each finding give file:line, what is wrong and a concrete fix.

- **Correctness:** edge cases, error paths, off-by-one, null handling.
- **Security:** untrusted input, secrets in code, injection, unsafe paths.
- **Clarity:** names, dead code, comments that no longer match the code.
- **Tests:** is the new behaviour covered, and would the tests catch a regression?

Rank findings by severity. Skip style nits unless they hide a bug.
`,
  },
];
