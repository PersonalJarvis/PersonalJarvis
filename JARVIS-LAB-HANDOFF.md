# JARVIS-LAB — Operational Handoff

This file is the persistent operational handoff for any AI agent or coding assistant working on **JARVIS LAB**.

## Source of truth

The repository, Git history, pull request state, CI/checks, tests, and current branch contents are the source of truth.

Do **not** assume that a task is complete only because this document, a chat, or a previous agent says so. Verify the real repository state first.

## Repository constraints

- Repository: `Sgangalossa/PersonalJarvis`
- Working branch: `jarvis-lab`
- Main integration PR: **#1**
- PR #1 must remain **open** and **draft**
- Do not merge into `main`
- Do not publish releases
- Do not force-push
- Do not rewrite or delete Git history
- Do not delete branches unless explicitly authorized
- Do not commit secrets, credentials, tokens, private keys, or sensitive data

## Mandatory startup check

Before making changes:

1. Confirm repository and remotes.
2. Confirm the active branch is `jarvis-lab`.
3. Read the current HEAD SHA.
4. Inspect recent commits.
5. Inspect PR #1 state.
6. Inspect current CI/checks.
7. Inspect recent changed files and relevant logs.
8. Reconstruct what is actually completed, in progress, blocked, and next.

If the repository contradicts any prior summary, **the repository wins**.

## Operating mode

Work on the project, not merely on planning it.

For each useful, non-blocked task:

1. inspect the necessary code and context;
2. implement the smallest coherent change;
3. add or update tests when appropriate;
4. run relevant tests, lint, typecheck, build, or validation when technically possible;
5. fix regressions or failures introduced by the change;
6. commit the work to `jarvis-lab`;
7. push/update the branch when permissions allow;
8. verify CI/checks and record the resulting SHA.

## Continuous execution rule

**Do not stop after completing a task if another useful non-blocked task exists.**

When one task is complete:

- identify the next priority task;
- inspect it;
- implement it;
- test it;
- commit it;
- continue.

Do not wait for the user to say “continue”, “proceed”, or an equivalent instruction.

Status summaries are informational only. They are **not** stopping points.

## Anti-stall rule

If HEAD has not advanced since the previous operational check and there is any useful non-blocked work available, do not finish with only:

- a status check;
- a summary;
- a plan;
- a “resume” message;
- a to-do list.

Start real work on the highest-priority non-blocked task.

Treat progress as verified only when there is concrete evidence, preferably one or more of:

- a new commit/SHA;
- changed code in the repository;
- tests executed;
- CI/check results;
- reproducible validation evidence.

## Autonomy

Within available permissions, autonomously perform normal reversible development actions, including:

- repository inspection;
- reading logs;
- editing code;
- bug fixes;
- refactoring;
- adding/updating tests;
- running tests;
- linting;
- typechecking;
- builds;
- CI inspection;
- commits;
- pushes to `jarvis-lab`;
- work associated with PR #1.

Do not ask for confirmation for ordinary reversible development work.

Request human intervention only when genuinely required, such as:

- OAuth or re-authentication;
- unavailable credentials or secrets;
- mandatory security consent;
- missing repository permissions;
- an irreversible/destructive action;
- a product decision that cannot reasonably be inferred.

## Quality requirements

Before considering a change complete:

- preserve architectural consistency;
- avoid unnecessary duplication;
- keep changes modular and reviewable;
- add tests where appropriate;
- check for regressions;
- run relevant validation;
- do not claim success without evidence.

## Safety boundaries

Never, without explicit user authorization:

- merge PR #1;
- merge into `main`;
- close PR #1;
- mark PR #1 ready for review;
- publish a release;
- force-push;
- rewrite history;
- delete branches;
- perform destructive infrastructure changes.

## Progress reporting

Keep user-facing updates short and operational. A useful format is:

```text
JARVIS LAB

✅ Completed
- ...

🔧 In progress
- ...

⏭ Next
- ...

🧪 Tests / CI
- ...

📍 HEAD
- <sha>

⚠️ Blocks
- none / real blocker
```

After reporting, continue working if useful non-blocked work remains.

## Goal

Continuously improve JARVIS LAB toward a stable, modular, verified system by identifying and resolving, as appropriate:

- bugs;
- missing functionality;
- architectural gaps;
- regressions;
- missing tests;
- technical debt;
- UI/UX issues;
- integration issues;
- reliability and maintainability opportunities.

## Instruction to a new agent

On first contact with this repository, do not reply with only a plan.

Read this file, inspect the real state of `jarvis-lab`, PR #1, HEAD, recent commits, and CI, then continue from the highest-priority useful non-blocked task.
