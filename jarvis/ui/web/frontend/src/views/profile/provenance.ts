/**
 * Where a fact came from — parsed out of USER.md's own audit trail.
 *
 * Every writer that touches the profile appends one line to the
 * `## Observations over time` section (see
 * `jarvis/memory/user_profile.py::append_observation`):
 *
 *     - [YYYY-MM-DD] <cluster>.<field>: <value> — "<evidence>"
 *
 * That line is the answer to the only question a profile page is really
 * asked: *how do you know that about me?* It is written by all three writers
 * — the regex capture bridge, the `update_profile` tool and the view's own
 * PATCH — so the trail is complete, and it has been on disk all along with
 * nothing reading it.
 *
 * Parsed on the client rather than served: `GET /api/profile/raw` is already
 * in the query cache (ProfileView holds it for the source tab), so this costs
 * one regex pass and no round trip. Side-effect free, tested in
 * provenance.test.ts.
 */

export interface Observation {
  /** ISO date, exactly as written: YYYY-MM-DD. */
  date: string;
  cluster: string;
  field: string;
  /** The value as it was written that day — may since have changed. */
  value: string;
  /** The sentence the fact was taken from. Empty when the writer had none. */
  evidence: string;
}

/**
 * One observation line. The value is non-greedy so the optional evidence tail
 * wins the em dash; a line whose label is not `<cluster>.<field>` (the free
 * notes the curator's merger writes) is skipped rather than guessed at.
 */
const LINE =
  /^-\s*\[(\d{4}-\d{2}-\d{2})\]\s+([a-z_]+)\.([a-z_]+):\s*(.*?)(?:\s+—\s+"([^"]*)")?\s*$/i;

const OBSERVATIONS_START = "<!-- curator:observations:start -->";
const OBSERVATIONS_END = "<!-- curator:observations:end -->";

/**
 * Every observation in the file, in the order it was written.
 *
 * Scoped to the curator's markers when they are present — the section's own
 * prose contains a `Format:` example that would otherwise parse as a fact.
 */
export function parseObservations(raw: string | null | undefined): Observation[] {
  if (!raw) return [];

  const start = raw.indexOf(OBSERVATIONS_START);
  const end = raw.indexOf(OBSERVATIONS_END);
  const body =
    start >= 0 && end > start ? raw.slice(start + OBSERVATIONS_START.length, end) : raw;

  const out: Observation[] = [];
  for (const line of body.split("\n")) {
    const m = LINE.exec(line.trim());
    if (!m) continue;
    out.push({
      date: m[1],
      cluster: m[2].toLowerCase(),
      field: m[3].toLowerCase(),
      value: m[4].trim(),
      evidence: (m[5] ?? "").trim(),
    });
  }
  return out;
}

/** The observations for one field, oldest first. */
export function historyFor(
  observations: readonly Observation[],
  cluster: string,
  field: string,
): Observation[] {
  return observations.filter((o) => o.cluster === cluster && o.field === field);
}

/**
 * The line that explains the value showing right now — the most recent one.
 * `null` when the fact predates the audit trail (imported, or written before
 * the observation section existed), which is why a row without provenance
 * shows no date at all rather than an invented one.
 */
export function latestFor(
  observations: readonly Observation[],
  cluster: string,
  field: string,
): Observation | null {
  const all = historyFor(observations, cluster, field);
  return all.length ? all[all.length - 1] : null;
}

/**
 * The categories the assistant refuses to store, read out of the file's own
 * `## Do Not Record` section instead of being restated here.
 *
 * Hardcoding them would let the page promise something the backend no longer
 * does. `_DO_NOT_RECORD` in `jarvis/plugins/tool/profile_update.py` is the
 * enforcement; this section is its written form, and the page quotes it.
 */
export function parseDoNotRecord(raw: string | null | undefined): string[] {
  if (!raw) return [];
  const heading = raw.search(/^##\s+Do Not Record\s*$/im);
  if (heading < 0) return [];

  const after = raw.slice(heading);
  const nextHeading = after.search(/\n##\s+/);
  const section = nextHeading > 0 ? after.slice(0, nextHeading) : after;

  const items: string[] = [];
  for (const line of section.split("\n")) {
    const m = /^-\s+(.*\S)\s*$/.exec(line.trim());
    if (m) items.push(m[1]);
  }
  return items;
}

/**
 * "Politics" out of "Political or religious beliefs (echo-chamber risk)".
 *
 * The full sentences are the promise; the chips are the glance. The rationale
 * in brackets is what makes each line long, and it is exactly the part a chip
 * does not need — it stays in the row's title attribute.
 */
export function shortenCategory(text: string): string {
  const withoutReason = text.replace(/\s*\([^)]*\)\s*$/, "").trim();
  // The connectives are English and German because the categories are
  // written in whichever language the profile template shipped in.
  const firstClause = withoutReason
    .split(/\s+(?:or|as|und|oder)\s+/i) // i18n-allow: clause splitter, not display text
    [0]
    .trim();
  return firstClause || withoutReason || text;
}
