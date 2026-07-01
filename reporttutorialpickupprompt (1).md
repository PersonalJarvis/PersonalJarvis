# Pick-up: Report-Tutorial — Secrets/PII review (LOCAL)

You are Claude Code running in the LOCAL, full Personal Jarvis working tree
(the real, non-depersonalized repo — CLAUDE.md §2 privacy gate applies).

A parallel cloud session was asked to review the "Report-Tutorial" for secrets
and "things that shouldn't be in there" (real name, personal paths, machine/user
IDs, internal project ids, API keys/tokens — the §2 categories). It runs on a
DEPERSONALIZED public snapshot, so it could NOT identify which artifact the
"Report-Tutorial" is — the relevant files aren't in the public snapshot.

YOUR JOB: investigate THIS local directory (read-only) and answer every question
below. Save the answers as a Markdown list to
`docs/handoffs/report-tutorial-review-answers.md` — each question as a heading,
the answer beneath, with `path:line` evidence where relevant. Do NOT modify,
commit, push, or run the public ship gate. Artifacts stay English (§1).

## A — Identify the artifact
1. What exactly is the "Report-Tutorial"? Give the exact path(s).
2. Is it a doc, a generated report, the onboarding tutorial, a feature that
   PRODUCES reports, or something else? Describe in one line.
3. Is it git-tracked, untracked, or generated at runtime?
4. Format/type: Markdown, code, HTML, screenshots, video, JSON output?
5. When was it last modified, and is it hand-written or generated?

## B — Publication intent
6. Is it meant to ship to the PUBLIC repo (PersonalJarvis/PersonalJarvis) or
   stay private/local only?
7. Is it on the distribution denylist or in .gitignore?

## C — Leak surface (the actual review)
8. Does it embed real data: logs, transcripts, console output, screenshots,
   file paths, example API responses, mission/worker outputs?
9. Search it for each of these and report every hit with path:line:
   - maintainer real name (Lütke / Luetke + first name) <!-- i18n-allow: proper name example -->
   - personal email(s) (e.g. *@gmail.com that isn't an intentional contact)
   - C:\Users\<name> or other personal filesystem paths
   - machine/user identifiers, Windows SIDs (S-1-5-21-...)
   - internal project ids
   - private IPs (192.168.* / 10.* / 172.16-31.*)
   - API keys / tokens / private-key blocks / JWTs
10. Any screenshots/images: do they show secrets, tokens, the personal desktop,
    window titles, file paths, or other identifying detail?
11. Any embedded URLs/IDs (YouTube, Discord invite, Telegram, gist, internal
    hostnames): intended-public or accidentally-private?

## D — Scope
12. Is this one file or a whole directory/section? List everything in scope.
13. Does the artifact contain anything the pii-scrub.tsv manifest would mask?
    (Run the local docs_privacy_scan.py / ship gate scanner against it and paste
    the result — it works locally because the manifest is present here.)
14. Was there a specific recent change/commit that triggered this concern?

## Output
Write `docs/handoffs/report-tutorial-review-answers.md` as a Markdown list with
all answers + evidence. Then tell Ruben it's ready so he can send it back.
