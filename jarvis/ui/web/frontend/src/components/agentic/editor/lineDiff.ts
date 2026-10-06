/**
 * Which lines of a buffer differ from the last commit, for the editor's
 * gutter markers: added (green), modified (blue) and deleted (a red notch
 * between two lines).
 *
 * A line-based Myers diff (O((N+M)·D)) over the two texts. Large rewrites are
 * not worth marking line by line, so the search gives up beyond a budget of
 * differences and the caller simply shows no markers.
 */
export type GutterKind = "added" | "modified" | "deleted";

export interface GutterChange {
  kind: GutterKind;
  /** 1-based first line in the current text (for "deleted": the line after the gap). */
  start: number;
  /** 1-based last line, inclusive (equals `start` for "deleted"). */
  end: number;
}

/** More differences than this and no markers are drawn. */
export const MAX_EDIT_DISTANCE = 1000;
/** Files longer than this (both sides together) get no markers. */
export const MAX_DIFF_LINES = 20_000;

const splitLines = (text: string) => text.split(/\r\n|\r|\n/);

/** The shortest edit script as [kind, oldIndex, newIndex] steps. */
function editScript(a: string[], b: string[]): ("=" | "-" | "+")[] | null {
  const n = a.length;
  const m = b.length;
  const max = Math.min(n + m, MAX_EDIT_DISTANCE);
  const offset = max + 1;
  const trace: Int32Array[] = [];
  let v = new Int32Array(2 * max + 3);
  for (let d = 0; d <= max; d += 1) {
    const next = v.slice();
    for (let k = -d; k <= d; k += 2) {
      let x =
        k === -d || (k !== d && v[offset + k - 1] < v[offset + k + 1]) ? v[offset + k + 1] : v[offset + k - 1] + 1;
      let y = x - k;
      while (x < n && y < m && a[x] === b[y]) {
        x += 1;
        y += 1;
      }
      next[offset + k] = x;
      if (x >= n && y >= m) {
        trace.push(next);
        return backtrack(trace, n, m, offset);
      }
    }
    trace.push(next);
    v = next;
  }
  return null;
}

function backtrack(trace: Int32Array[], n: number, m: number, offset: number): ("=" | "-" | "+")[] {
  const steps: ("=" | "-" | "+")[] = [];
  let x = n;
  let y = m;
  for (let d = trace.length - 1; d > 0; d -= 1) {
    const v = trace[d - 1];
    const k = x - y;
    const down = k === -d || (k !== d && v[offset + k - 1] < v[offset + k + 1]);
    const prevK = down ? k + 1 : k - 1;
    const prevX = v[offset + prevK];
    const prevY = prevX - prevK;
    while (x > prevX && y > prevY) {
      steps.push("=");
      x -= 1;
      y -= 1;
    }
    steps.push(down ? "+" : "-");
    x = prevX;
    y = prevY;
  }
  while (x > 0 && y > 0) {
    steps.push("=");
    x -= 1;
    y -= 1;
  }
  return steps.reverse();
}

/** The gutter markers for `current` against `base`; null when there are too many. */
export function gutterChanges(base: string, current: string): GutterChange[] | null {
  if (base === current) return [];
  const a = splitLines(base);
  const b = splitLines(current);
  if (a.length + b.length > MAX_DIFF_LINES) return null;
  const steps = editScript(a, b);
  if (!steps) return null;
  const changes: GutterChange[] = [];
  let line = 1; // 1-based line in `current`
  let index = 0;
  while (index < steps.length) {
    if (steps[index] === "=") {
      line += 1;
      index += 1;
      continue;
    }
    let removed = 0;
    let added = 0;
    while (index < steps.length && steps[index] !== "=") {
      if (steps[index] === "-") removed += 1;
      else added += 1;
      index += 1;
    }
    if (added === 0) {
      changes.push({ kind: "deleted", start: line, end: line });
    } else {
      const kind: GutterKind = removed > 0 ? "modified" : "added";
      changes.push({ kind, start: line, end: line + added - 1 });
      line += added;
    }
  }
  return changes;
}
