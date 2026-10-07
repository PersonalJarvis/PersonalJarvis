import type { AgentSearchDocument, AgentSearchMatch } from "./agentSearch";

export function normalizeSearchText(text: string): string {
  return text
    .replace(/(\p{Ll})(\p{Lu})/gu, "$1 $2")
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .replace(/ß/g, "ss") // i18n-allow: Unicode case folding.
    .match(/[\p{L}\p{N}]+/gu)?.join(" ") ?? "";
}

/** Bounded edit distance, including adjacent swapped letters. */
function typoDistance(left: string, right: string, limit: number): number {
  if (Math.abs(left.length - right.length) > limit) return limit + 1;
  let previous = Array.from({ length: right.length + 1 }, (_, index) => index);
  let beforePrevious = previous;
  for (let row = 1; row <= left.length; row += 1) {
    const current = [row];
    for (let col = 1; col <= right.length; col += 1) {
      current[col] = Math.min(current[col - 1] + 1, previous[col] + 1, previous[col - 1] + (left[row - 1] === right[col - 1] ? 0 : 1));
      if (row > 1 && col > 1 && left[row - 1] === right[col - 2] && left[row - 2] === right[col - 1]) {
        current[col] = Math.min(current[col], beforePrevious[col - 2] + 1);
      }
    }
    if (Math.min(...current) > limit) return limit + 1;
    beforePrevious = previous;
    previous = current;
  }
  return previous[right.length];
}

/** Immediate matches: exact names/phrases, all words, prefixes, then typos. */
export function lexicalAgentMatches(query: string, documents: AgentSearchDocument[]): AgentSearchMatch[] {
  const normalized = normalizeSearchText(query);
  if (!normalized) return [];
  const terms = [...new Set(normalized.split(" "))];
  const matches: AgentSearchMatch[] = [];
  for (const document of documents) {
    const name = normalizeSearchText(document.name ?? "");
    const fields = [name, ...document.texts.map(normalizeSearchText)].filter(Boolean);
    let score = 0;
    if (name === normalized) score = 500;
    else if (fields.includes(normalized)) score = 400;
    else if (fields.some((field) => ` ${field} `.includes(` ${normalized} `))) score = 300;
    else {
      const words = [...new Set(fields.flatMap((field) => field.split(" ")))];
      const qualities = terms.map((term, index) => {
        if (words.includes(term)) return 1;
        // Short acronyms and identifiers stay exact, regardless of casing.
        if (/\p{N}/u.test(term)) return 0;
        if (index === terms.length - 1 && term.length >= 4 && words.some((word) => word.startsWith(term))) return 0.8;
        if (term.length < 5) return 0;
        const limit = term.length >= 9 ? 2 : 1;
        return words.some((word) => typoDistance(term, word, limit) <= limit) ? 0.6 : 0;
      });
      // Never drop a query word merely to manufacture a result.
      if (qualities.every((quality) => quality > 0)) {
        const weakest = Math.min(...qualities);
        score = (weakest === 1 ? 200 : weakest === 0.8 ? 100 : 50) + qualities.reduce<number>((sum, quality) => sum + quality, 0) / terms.length;
      }
    }
    if (score) matches.push({ id: document.id, score });
  }
  return matches.sort((left, right) => right.score - left.score);
}

/** The semantic stage can add results, but cannot move word hits or duplicate them. */
export function mergeAgentMatches(lexical: AgentSearchMatch[], semantic: AgentSearchMatch[]): AgentSearchMatch[] {
  const seen = new Set(lexical.map(({ id }) => id));
  return [...lexical, ...semantic.filter(({ id }) => {
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  })];
}

/** Known one-word lookups and pane names need no model download or inference. */
export function needsSemanticSearch(query: string, documents: AgentSearchDocument[], lexical: AgentSearchMatch[]): boolean {
  const normalized = normalizeSearchText(query);
  return normalized.length >= 3
    && !/^[\p{L}]*\d+$/u.test(normalized)
    && !documents.some((document) => document.name && normalizeSearchText(document.name) === normalized)
    && (normalized.includes(" ") || lexical.length === 0);
}
