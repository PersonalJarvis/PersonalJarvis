/**
 * Quick Open's matching: the typed letters must appear in the path in order;
 * hits in the file name, at word starts and in runs rank higher.
 */

const WORD_START = new Set(["/", "_", "-", ".", " "]);

/** A score for `path` against a lowercased, space-free `query`; null when it does not match. */
export function scorePath(path: string, query: string): number | null {
  if (!query) return 0;
  const lower = path.toLowerCase();
  const nameStart = lower.lastIndexOf("/") + 1;
  const name = lower.slice(nameStart);
  let score = 0;
  let from = 0;
  let previous = -2;
  for (const char of query) {
    const index = lower.indexOf(char, from);
    if (index < 0) return null;
    score += 1;
    if (index === previous + 1) score += 3;
    if (index >= nameStart) score += 2;
    if (index === 0 || WORD_START.has(lower[index - 1])) score += 2;
    previous = index;
    from = index + 1;
  }
  if (name.startsWith(query)) score += 15;
  else if (name.includes(query)) score += 10;
  // Shorter paths first among equals: `src/app.ts` before `src/legacy/old/app.ts`.
  return score - path.length * 0.01;
}

export interface QuickOpenQuery {
  needle: string;
  line?: number;
  column?: number;
}

/** Split `main.py:42:7` into what to match and where to jump. */
export function parseQuickOpen(raw: string): QuickOpenQuery {
  const trimmed = raw.trim();
  const match = /^(.*?):(\d+)(?::(\d+))?$/.exec(trimmed);
  const text = match ? match[1] : trimmed;
  return {
    needle: text.toLowerCase().replace(/\\/g, "/").replace(/\s+/g, ""),
    line: match ? Number(match[2]) : undefined,
    column: match?.[3] ? Number(match[3]) : undefined,
  };
}

/** The best `limit` paths for a query, best first. */
export function rankPaths(paths: readonly string[], needle: string, limit = 50): string[] {
  if (!needle) return paths.slice(0, limit);
  const scored: { path: string; score: number }[] = [];
  for (const path of paths) {
    const score = scorePath(path, needle);
    if (score !== null) scored.push({ path, score });
  }
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, limit).map((entry) => entry.path);
}
