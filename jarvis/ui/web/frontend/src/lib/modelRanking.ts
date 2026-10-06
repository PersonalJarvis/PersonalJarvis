import type { CuratedModel } from "@/lib/agentChatApi";

/**
 * Splits a provider's model list into the current lineup and older models.
 *
 * Each model line ("Claude Opus", "Gemini Pro", "GPT Sol") keeps only its
 * newest version in the lineup; earlier versions of that line, and named
 * aliases without a version, move to an "older models" fold. The lineup is
 * sorted newest version first, ties keeping the catalog's own order.
 *
 * Nothing is folded when the list carries no versions at all (Cursor's
 * "Auto", a user's own aliases): there is no evidence of what is older.
 */

export interface RankedModels<T extends CuratedModel = CuratedModel> {
  current: T[];
  older: T[];
}

interface Parsed {
  line: string;
  version: number[];
}

const VERSION = /(\d+(?:[.-]\d+)*)/;
/** Snapshot dates in ids (`-20251001`) and context sizes (`[1m]`) are not versions. */
const NOISE = /(?:[-_]?\d{8}\b)|\[[^\]]*\]|\(\s*[^)]*\)/g;

function parse(model: CuratedModel): Parsed | null {
  for (const source of [model.label, model.id.split("/").pop() ?? model.id]) {
    const text = (source ?? "").replace(NOISE, " ").trim();
    const match = VERSION.exec(text);
    if (!match) continue;
    const version = match[1].split(/[.-]/).map(Number);
    if (version.some((part) => !Number.isFinite(part)) || version[0] > 999) continue;
    const line = (text.slice(0, match.index) + " " + text.slice(match.index + match[0].length))
      .toLocaleLowerCase()
      .replace(/[^a-z]+/g, " ")
      .trim();
    return { line, version };
  }
  return null;
}

function compareVersions(a: number[], b: number[]): number {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const diff = (a[i] ?? 0) - (b[i] ?? 0);
    if (diff) return diff;
  }
  return 0;
}

export function rankModels<T extends CuratedModel>(models: T[]): RankedModels<T> {
  const parsed = models.map((model) => ({ model, info: model.id ? parse(model) : null }));
  if (!parsed.some((entry) => entry.info)) return { current: [...models], older: [] };

  const newest = new Map<string, number[]>();
  for (const { info } of parsed) {
    if (!info) continue;
    const best = newest.get(info.line);
    if (!best || compareVersions(info.version, best) > 0) newest.set(info.line, info.version);
  }

  const byVersion = (a: (typeof parsed)[number], b: (typeof parsed)[number]) =>
    compareVersions(b.info?.version ?? [], a.info?.version ?? []);
  const current: typeof parsed = [];
  const older: typeof parsed = [];
  for (const entry of parsed) {
    // The provider's own default ("" id) always leads.
    if (!entry.model.id) current.unshift(entry);
    else if (entry.info && compareVersions(entry.info.version, newest.get(entry.info.line)!) === 0) current.push(entry);
    else older.push(entry);
  }
  const head = current.filter((entry) => !entry.model.id);
  const rest = current.filter((entry) => entry.model.id).sort(byVersion);
  return {
    current: [...head, ...rest].map((entry) => entry.model),
    // Array.prototype.sort is stable, so equal versions keep catalog order.
    older: older.sort(byVersion).map((entry) => entry.model),
  };
}
