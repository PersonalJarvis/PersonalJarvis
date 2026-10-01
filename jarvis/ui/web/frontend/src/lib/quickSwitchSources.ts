/**
 * Ranking for the quick switcher's live results — chats, terminals and
 * workspaces — next to the fixed section list in `./quickSwitch`.
 *
 * These are free text (a chat's title, a terminal's last prompt), so a query
 * of several words matches when EVERY word is found somewhere in the item:
 * "browser accept" finds the chat "Browser acceptance". Each word is scored
 * with the section ranking's own rules (exact > prefix > word start >
 * initials > mid-word from four letters), so "ide" still does not find
 * "provide" here either.
 */
import { normalizeQuery, scoreTerm } from "@/lib/quickSwitch";

/** 0 when some word of the query is found nowhere in `fields`. */
export function matchScore(query: string, fields: readonly (string | null | undefined)[]): number {
  const words = normalizeQuery(query).split(/\s+/).filter(Boolean);
  if (words.length === 0) return 0;
  const texts = fields.map((field) => normalizeQuery(field ?? "")).filter(Boolean);
  let total = 0;
  for (const word of words) {
    const best = Math.max(0, ...texts.map((text) => scoreTerm(text, word)));
    if (best === 0) return 0;
    total += best;
  }
  return total / words.length;
}

/**
 * The items that match, best first, at most `limit`. Ties keep the input
 * order, which the callers make "most recent first".
 */
export function rankItems<T>(
  query: string,
  items: readonly T[],
  fields: (item: T) => readonly (string | null | undefined)[],
  limit: number,
): T[] {
  return items
    .map((item, index) => ({ item, index, score: matchScore(query, fields(item)) }))
    .filter((row) => row.score > 0)
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .slice(0, limit)
    .map((row) => row.item);
}

/** The last path segment of a folder, for a short "where" next to a name. */
export function folderName(folder: string): string {
  const parts = folder.split(/[\\/]+/).filter(Boolean);
  return parts[parts.length - 1] ?? folder;
}
