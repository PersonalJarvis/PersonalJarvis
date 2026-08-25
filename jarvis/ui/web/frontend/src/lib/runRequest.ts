/**
 * The user's real ask out of a run's recorded request — what a rail row, a
 * stage title and an output page call the run.
 *
 * The run list (`/api/outputs`) already strips the standing quality
 * directive the mission builder prepends (`clean_request_body`). What it
 * leaves in place is the TAIL the builder appends on a no-interpretation
 * spawn: "Supporting context from the recent conversation (use only to
 * resolve references; …):" followed by the transcript hints. That tail is
 * plumbing for the worker, not part of the request, and it made half the
 * rail read "…Supporting context from the recent conversation (use only to
 * resolve references; the underlying request remains authoritative): - …"
 * (2026-08-25). Pure text; no I/O.
 */

/** The builder's supporting-context tail — everything from its lead on. */
const SUPPORTING_CONTEXT_RE =
  /\s*Supporting context from the recent conversation\b[\s\S]*$/i;

/** The user's request without the worker-facing tail, whitespace collapsed. */
export function cleanRequest(utterance: string | null | undefined): string {
  const text = (utterance ?? "").replace(SUPPORTING_CONTEXT_RE, "");
  return text.replace(/\s+/g, " ").trim();
}

/** Default headline length — one comfortable line at the stage's h1 size. */
export const HEADLINE_MAX_CHARS = 96;

/**
 * A headline out of a request: its first sentence when that is short enough,
 * otherwise the first `max` characters cut at a word boundary with an
 * ellipsis. A request that is already short comes back whole.
 */
export function requestHeadline(text: string, max: number = HEADLINE_MAX_CHARS): string {
  const clean = cleanRequest(text);
  if (clean.length <= max) return clean;
  const sentence = /^(.{12,}?[.!?])\s/.exec(clean)?.[1];
  if (sentence && sentence.length <= max) return sentence;
  const cut = clean.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return `${(space > max / 2 ? cut.slice(0, space) : cut).replace(/[\s,;:\-–—(]+$/, "")}…`;
}
