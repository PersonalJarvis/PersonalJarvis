/**
 * Relationship enum — TypeScript mirror (Layers 4 + 5 of the five-layer
 * anti-drift pattern, docs/anti-drift-three-layer.md).
 *
 * The source of truth is the Python tuple in `jarvis/contacts/schema.py`
 * (`RELATIONSHIPS`). A parity test
 * (`tests/unit/contacts/test_relationship_parity.py`) asserts this array equals
 * it and that every value has a `RELATIONSHIP_LABELS` entry — so a value added
 * on one side without the other fails CI (the BUG-008 / AP-4 defense).
 */
export const RELATIONSHIPS = [
  "family",
  "friend",
  "colleague",
  "partner",
  "acquaintance",
  "other",
] as const;

export type Relationship = (typeof RELATIONSHIPS)[number];

/** Default English labels (Layer 5). The i18n `contacts.rel.*` keys carry the
 *  translated forms; this map is the fallback + the parity-test target. */
export const RELATIONSHIP_LABELS: Record<Relationship, string> = {
  family: "Family",
  friend: "Friend",
  colleague: "Colleague",
  partner: "Partner",
  acquaintance: "Acquaintance",
  other: "Other",
};

/** Translate a relationship via the i18n `contacts.rel.*` keys, falling back to
 *  the English default when a translation is missing. */
export function relationshipLabel(
  t: (key: string) => string,
  rel: Relationship | null | undefined,
): string {
  if (!rel) return "";
  const key = `contacts.rel.${rel}`;
  const translated = t(key);
  return translated && translated !== key ? translated : RELATIONSHIP_LABELS[rel];
}
