/**
 * Ledger logic — the pure math and vocabulary behind ProfileView.
 *
 * ProfileView renders Jarvis' knowledge about the user as a kept ledger:
 * named acquaintance stages instead of a bare percentage, a prioritized list
 * of "still unwritten" questions, and a generative sigil whose geometry is
 * derived from which fields are inked. Everything here is side-effect free
 * and unit-tested in ledger.test.ts; the component stays thin.
 */

export type ClusterId =
  | "identity"
  | "communication"
  | "work_style"
  | "values"
  | "relationship";

// The field vocabulary mirrors the YAML frontmatter clusters the Curator
// writes into USER.md (see jarvis/ui/web/profile_routes.py → profile.meta).
export const CLUSTER_FIELD_KEYS: Record<ClusterId, string[]> = {
  identity: [
    "name",
    "preferred_address",
    "pronouns",
    "primary_language",
    "languages",
    "timezone",
    "devices",
  ],
  communication: ["directness", "formality", "verbosity", "humor_types", "emoji_ok"],
  work_style: ["focus_mode", "planning_horizon"],
  values: ["top_values", "pet_peeves", "motivations"],
  relationship: ["feedback_pref"],
};

export const CLUSTER_ORDER: ClusterId[] = [
  "identity",
  "communication",
  "work_style",
  "values",
  "relationship",
];

// Field shapes — drive the inline editor. A list field is edited as removable
// chips (append/remove one item); a bool field as a yes/no toggle; everything
// else as a single text input. These MUST mirror _LIST_FIELDS / _BOOL_FIELDS in
// jarvis/plugins/tool/profile_update.py (the backend rejects a mismatched
// operation with 400) — the parity is pinned by test_profile_update.py.
export const LIST_FIELD_KEYS: ReadonlySet<string> = new Set([
  "languages",
  "devices",
  "humor_types",
  "top_values",
  "pet_peeves",
  "motivations",
]);

export const BOOL_FIELD_KEYS: ReadonlySet<string> = new Set(["emoji_ok"]);

export type FieldKind = "scalar" | "list" | "bool";

export function isListField(field: string): boolean {
  return LIST_FIELD_KEYS.has(field);
}

export function isBoolField(field: string): boolean {
  return BOOL_FIELD_KEYS.has(field);
}

export function fieldKind(field: string): FieldKind {
  if (LIST_FIELD_KEYS.has(field)) return "list";
  if (BOOL_FIELD_KEYS.has(field)) return "bool";
  return "scalar";
}

export const TOTAL_FIELDS: number = CLUSTER_ORDER.reduce(
  (acc, cid) => acc + CLUSTER_FIELD_KEYS[cid].length,
  0,
);

/** The order in which the butler would ask — most identity-anchoring first. */
export const ASK_PRIORITY: string[] = [
  "name",
  "preferred_address",
  "primary_language",
  "directness",
  "humor_types",
  "focus_mode",
  "top_values",
  "feedback_pref",
  "formality",
  "verbosity",
  "emoji_ok",
  "languages",
  "timezone",
  "planning_horizon",
  "pet_peeves",
  "motivations",
  "pronouns",
  "devices",
];

const FIELD_CLUSTER: Record<string, ClusterId> = (() => {
  const map: Record<string, ClusterId> = {};
  for (const cid of CLUSTER_ORDER) {
    for (const key of CLUSTER_FIELD_KEYS[cid]) map[key] = cid;
  }
  return map;
})();

// ----------------------------------------------------------------------
// Emptiness + fill counting
// ----------------------------------------------------------------------

export function isEmptyValue(value: unknown): boolean {
  return (
    value === undefined ||
    value === null ||
    value === "" ||
    (Array.isArray(value) && value.length === 0)
  );
}

function clusterData(
  meta: Record<string, unknown>,
  cluster: ClusterId,
): Record<string, unknown> {
  const raw = meta[cluster];
  return raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
}

/** Number of vocabulary fields with a non-empty value. Stray keys never count. */
export function countFilled(meta: Record<string, unknown>): number {
  let filled = 0;
  for (const cid of CLUSTER_ORDER) {
    const data = clusterData(meta, cid);
    for (const key of CLUSTER_FIELD_KEYS[cid]) {
      if (!isEmptyValue(data[key])) filled += 1;
    }
  }
  return filled;
}

/** Number of vocabulary fields with a non-empty value inside one cluster. */
export function clusterFilledCount(
  meta: Record<string, unknown>,
  cluster: ClusterId,
): number {
  const data = clusterData(meta, cluster);
  let filled = 0;
  for (const key of CLUSTER_FIELD_KEYS[cluster]) {
    if (!isEmptyValue(data[key])) filled += 1;
  }
  return filled;
}

// ----------------------------------------------------------------------
// Acquaintance stages — relationship depth with a name, not a bare percent
// ----------------------------------------------------------------------

export interface StageInfo {
  index: 0 | 1 | 2 | 3 | 4 | 5;
  /** i18n suffix under profile_view.stages.* */
  key:
    | "blank_page"
    | "first_impressions"
    | "getting_acquainted"
    | "well_acquainted"
    | "trusted_company"
    | "inner_circle";
}

export function acquaintanceStage(filled: number, total: number): StageInfo {
  if (total <= 0 || filled <= 0) return { index: 0, key: "blank_page" };
  const pct = (filled / total) * 100;
  if (pct >= 100) return { index: 5, key: "inner_circle" };
  if (pct >= 75) return { index: 4, key: "trusted_company" };
  if (pct >= 50) return { index: 3, key: "well_acquainted" };
  if (pct >= 25) return { index: 2, key: "getting_acquainted" };
  return { index: 1, key: "first_impressions" };
}

// ----------------------------------------------------------------------
// Open questions — what the butler asks next
// ----------------------------------------------------------------------

export interface OpenQuestion {
  cluster: ClusterId;
  field: string;
}

export function collectOpenQuestions(
  meta: Record<string, unknown>,
  limit: number,
): OpenQuestion[] {
  const open: OpenQuestion[] = [];
  for (const field of ASK_PRIORITY) {
    if (open.length >= limit) break;
    const cluster = FIELD_CLUSTER[field];
    if (!cluster) continue;
    if (isEmptyValue(clusterData(meta, cluster)[field])) {
      open.push({ cluster, field });
    }
  }
  return open;
}

// ----------------------------------------------------------------------
// displayAddress — how the page addresses the user
// ----------------------------------------------------------------------

/**
 * The warmest available form of address: the user's preferred_address if
 * they ever stated one ("Chef"), otherwise their first name, otherwise null.
 */
export function displayAddress(
  meta: Record<string, unknown>,
  name: string | null,
): string | null {
  const preferred = clusterData(meta, "identity")["preferred_address"];
  if (typeof preferred === "string" && preferred.trim()) return preferred.trim();
  const first = (name ?? "").trim().split(/\s+/)[0];
  return first ? first : null;
}

