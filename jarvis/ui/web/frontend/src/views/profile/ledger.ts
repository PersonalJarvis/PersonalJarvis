/**
 * Ledger logic — the pure math and vocabulary behind ProfileView.
 *
 * The vocabulary (which fields exist, and what shape each one is), the
 * emptiness rules, and the fill counts the sections show. Everything here is
 * side-effect free and unit-tested in ledger.test.ts; the components stay
 * thin.
 *
 * The acquaintance stages and the prioritised question queue were removed
 * with the ask card: the page states facts and folds gaps now, so a named
 * stage ("First impressions") was a score standing where a fact belongs.
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

