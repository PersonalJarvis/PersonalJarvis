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


// ----------------------------------------------------------------------
// Choice fields — the closed vocabularies the profile template documents
// ----------------------------------------------------------------------

/**
 * Fields whose value is one of a few words, as written in the USER.md
 * template comments. The editor offers them as a segmented control; a value
 * outside the list (the curator wrote free text) still renders verbatim and
 * stays editable, so nothing on disk is ever hidden by the vocabulary.
 */
export const CHOICE_FIELDS: Readonly<Record<string, readonly string[]>> = {
  verbosity: ["tldr", "normal", "deep-dive"],
  focus_mode: ["deep-work", "fragment", "mixed"],
  planning_horizon: ["now", "today", "week", "quarter"],
  feedback_pref: ["direct-correct", "suggest", "ask-then-act"],
};

/** 1–5 scales; the template defines both ends, the editor shows five steps. */
export const SCALE_FIELDS: ReadonlySet<string> = new Set(["directness", "formality"]);

/** Suggested chips for list fields whose template names a vocabulary. */
export const LIST_SUGGESTIONS: Readonly<Record<string, readonly string[]>> = {
  humor_types: ["dry", "nerdy", "sarcastic", "warm", "none"],
  motivations: ["mastery", "autonomy", "impact"],
};

/** A scale value as an integer 1–5, or null when it is not one. */
export function scaleValue(value: unknown): number | null {
  const n = typeof value === "number" ? value : Number(String(value ?? "").trim());
  return Number.isInteger(n) && n >= 1 && n <= 5 ? n : null;
}

// ----------------------------------------------------------------------
// Page groups — how the five clusters are presented
// ----------------------------------------------------------------------

export type GroupId = "about" | "talk" | "work" | "values";

export interface FieldRef {
  cid: ClusterId;
  field: string;
}

/**
 * Four groups a reader recognises, built from the five storage clusters.
 * Relationship holds a single field (how you like feedback), which belongs
 * with how you work; a section with one row reads as an accident.
 */
export const PAGE_GROUPS: readonly { id: GroupId; fields: readonly FieldRef[] }[] = [
  { id: "about", fields: CLUSTER_FIELD_KEYS.identity.map((field) => ({ cid: "identity", field })) },
  {
    id: "talk",
    fields: CLUSTER_FIELD_KEYS.communication.map((field) => ({ cid: "communication", field })),
  },
  {
    id: "work",
    fields: [
      ...CLUSTER_FIELD_KEYS.work_style.map((field) => ({ cid: "work_style" as const, field })),
      ...CLUSTER_FIELD_KEYS.relationship.map((field) => ({ cid: "relationship" as const, field })),
    ],
  },
  { id: "values", fields: CLUSTER_FIELD_KEYS.values.map((field) => ({ cid: "values", field })) },
];

/** Whole days between an ISO date and now; null when unparseable. */
export function daysSince(iso: unknown, now: Date = new Date()): number | null {
  if (typeof iso !== "string" || !iso.trim()) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;
  return Math.max(0, Math.floor((now.getTime() - then.getTime()) / 86_400_000));
}

/** Fields whose values are language codes ("de"), shown by name ("German"). */
export const LANGUAGE_FIELDS: ReadonlySet<string> = new Set(["primary_language", "languages"]);

/** "de" -> "German" in the interface language; the value itself when unknown. */
export function languageName(code: string, ui: string): string {
  const tag = code.trim();
  if (!/^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$/i.test(tag)) return code;
  try {
    return new Intl.DisplayNames([ui], { type: "language" }).of(tag) ?? code;
  } catch {
    // Intl rejects some well-formed but unknown tags; the raw value is honest.
    return code;
  }
}
