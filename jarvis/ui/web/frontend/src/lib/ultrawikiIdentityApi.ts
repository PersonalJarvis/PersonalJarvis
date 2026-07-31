/**
 * Typed fetchers for the UltraWiki identity surface
 * (`jarvis/ui/web/ultrawiki_identity_routes.py`).
 *
 * The identity layer decides which mentions across the corpus are the SAME
 * person. It merges on its own ONLY on deterministic evidence — a shared
 * e-mail, phone number or address-book entry — and everything weaker becomes a
 * proposal a human decides. That split is the whole contract this client
 * carries into the UI:
 *
 * - a merge that already happened is REVERSIBLE (`unmergeIdentity`),
 * - a rejection is PERMANENT (there is no un-reject route, by design: the
 *   pair is never proposed again, which is what stops the queue from asking
 *   the same settled question forever),
 * - and `confirmIdentityMerge` hands back the very `merge_id` that reverses
 *   it, so the UI can offer the undo without a second lookup.
 *
 * Like the rest of the UltraWiki surface, 409 means "Ultra mode is off" or
 * "the store refused this operation" and 503 means the service is not wired
 * yet. Both arrive as an {@link UltraWikiApiError} carrying the backend's own
 * sentence — never re-worded here, because the store is the one authority on
 * why it said no.
 */

import { UltraWikiApiError } from "@/lib/ultrawikiApi";

// ---------------------------------------------------------------------------
// Value sets — mirrors of jarvis/ultrawiki/identity.py
// ---------------------------------------------------------------------------
//
// Five-layer anti-drift discipline (AP-4 / BUG-008): these cross Python → SQL
// CHECK → REST → TypeScript → UI, and are pinned against the Python enums by
// tests/unit/ultrawiki/test_identity_ui_parity.py. Never retype them elsewhere.

/** What an entity *is*. The People view only ever lists `person`. */
export const ULTRAWIKI_ENTITY_KINDS = [
  "person",
  "place",
  "org",
  "project",
  "topic",
] as const;
export type UltraWikiEntityKind = (typeof ULTRAWIKI_ENTITY_KINDS)[number];

/** One raw handle mapped onto an entity — the "facts" a person is known by. */
export const ULTRAWIKI_IDENTIFIER_KINDS = [
  "email",
  "phone",
  "contact",
  "handle",
  "name",
] as const;
export type UltraWikiIdentifierKind =
  (typeof ULTRAWIKI_IDENTIFIER_KINDS)[number];

/** How strong the evidence linking two identities is. */
export const ULTRAWIKI_MATCH_TIERS = [
  "deterministic",
  "probable",
  "weak",
] as const;
export type UltraWikiMatchTier = (typeof ULTRAWIKI_MATCH_TIERS)[number];

/** State of one confirmation-queue row. `rejected` is permanent by design. */
export const ULTRAWIKI_QUEUE_STATUSES = [
  "pending",
  "confirmed",
  "rejected",
] as const;
export type UltraWikiQueueStatus = (typeof ULTRAWIKI_QUEUE_STATUSES)[number];

/** The queue filter the REST layer accepts, plus its "every status" value. */
export type UltraWikiQueueFilter = UltraWikiQueueStatus | "all";

// ---------------------------------------------------------------------------
// Payloads
// ---------------------------------------------------------------------------

/**
 * Honest counters that travel with EVERY list answer.
 *
 * They are what turns an empty list into a diagnosis: `people: 0` means
 * nothing was ever seeded, while `people: 40` on an empty list means the
 * filter matched nobody — two different problems that look identical without
 * the counters.
 */
export interface UltraWikiIdentityCounts {
  entities: number;
  people: number;
  identifiers: number;
  pending_confirmations: number;
  merges: number;
}

/** One row of the People list. */
export interface UltraWikiPersonRow {
  id: number;
  kind: UltraWikiEntityKind | string;
  display_name: string;
  source_ref: string;
  identifier_count: number;
  created_at: string;
  updated_at: string;
}

/** One thing a person is known by (an e-mail, a phone number, a name…). */
export interface UltraWikiIdentifier {
  id: number;
  kind: UltraWikiIdentifierKind | string;
  value: string;
  /** The spelling a human should see; `value` is the normalized match key. */
  display_value: string;
  source_ref: string;
}

/** One reason two identities were linked — or proposed. */
export interface UltraWikiMatchEvidence {
  tier: UltraWikiMatchTier | string;
  /** e.g. `email`, `email_exact`, `name_similar` — see {@link evidenceParts}. */
  kind: string;
  value: string;
  score: number;
}

/** The two sides of a proposal, as much as a card needs to name them. */
export interface UltraWikiEntityStub {
  id: number;
  kind: UltraWikiEntityKind | string;
  display_name: string;
}

/** One merge that happened — the audit trail row, and the undo handle. */
export interface UltraWikiMergeRecord {
  id: number;
  winner_id: number;
  loser_id: number;
  tier: UltraWikiMatchTier | string;
  reason: string;
  evidence: UltraWikiMatchEvidence[];
  queue_id: number | null;
  merged_at: string;
  /** Non-null once the merge was undone — the row stays as evidence. */
  undone_at: string | null;
}

/** One pair the layer thinks MIGHT be the same person. Nothing is merged yet. */
export interface UltraWikiMergeProposal {
  id: number;
  status: UltraWikiQueueStatus | string;
  score: number;
  evidence: UltraWikiMatchEvidence[];
  created_at: string;
  updated_at: string;
  decided_at: string | null;
  decided_by: string | null;
  left: UltraWikiEntityStub;
  right: UltraWikiEntityStub;
}

/** Everything the identity layer holds about one person. */
export interface UltraWikiPerson {
  id: number;
  kind: UltraWikiEntityKind | string;
  display_name: string;
  canonical_key: string;
  merged_into: number | null;
  source_ref: string;
  profile: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  identifiers: UltraWikiIdentifier[];
  emails: string[];
  phones: string[];
  handles: string[];
  names: string[];
  contacts: string[];
  /** Identities folded INTO this one by a merge. */
  merged_from: Array<{ id: number; display_name: string }>;
  merges: UltraWikiMergeRecord[];
  pending_proposals: UltraWikiMergeProposal[];
  /** The id that was asked for — differs from `id` after a forward. */
  requested_id: number;
}

export interface UltraWikiPeoplePage {
  ok: boolean;
  people: UltraWikiPersonRow[];
  query: string;
  limit: number;
  offset: number;
  counts: Partial<UltraWikiIdentityCounts>;
}

export interface UltraWikiPersonAnswer {
  ok: boolean;
  person: UltraWikiPerson;
  /** True when a merged-away id was forwarded to the survivor. */
  forwarded: boolean;
}

export interface UltraWikiQueuePage {
  ok: boolean;
  proposals: UltraWikiMergeProposal[];
  status: string;
  limit: number;
  counts: Partial<UltraWikiIdentityCounts>;
}

export interface UltraWikiMergeLogPage {
  ok: boolean;
  merges: UltraWikiMergeRecord[];
  entity_id: number | null;
  limit: number;
  counts: Partial<UltraWikiIdentityCounts>;
}

/** What one seeding pass changed. Re-running is safe: `created` drops to 0. */
export interface UltraWikiSeedReport {
  created: number;
  linked: number;
  identifiers_added: number;
  merged: number;
  queued: number;
  skipped: number;
}

export interface UltraWikiSeedAnswer {
  ok: boolean;
  report: UltraWikiSeedReport;
  counts: Partial<UltraWikiIdentityCounts>;
}

export interface UltraWikiConfirmAnswer {
  ok: boolean;
  queue_id: number;
  /** The audit id that reverses this merge; 0 = the pair was already one. */
  merge_id: number;
}

export interface UltraWikiRejectAnswer {
  ok: boolean;
  queue_id: number;
  status: string;
}

export interface UltraWikiUnmergeAnswer {
  ok: boolean;
  merge_id: number;
  status: string;
}

/** One participant of an episodic event. */
export interface UltraWikiEventParticipant {
  entity_id: number | null;
  display_name: string;
}

/**
 * One episodic event (`GET /api/ultrawiki/events`).
 *
 * The person profile answers "what do you know about them" in two halves:
 * the identifiers above are the standing FACTS, these are the things that
 * HAPPENED. `date_label` is the backend's own rendering of `occurred_at` at
 * the precision the source actually supported, so the UI never invents a day
 * for an event the source only pinned to a month.
 */
export interface UltraWikiPersonEvent {
  id: number;
  item_id: number;
  kind: string;
  title: string;
  summary: string;
  occurred_at: string;
  occurred_end: string;
  occurred_precision: string;
  time_anchor: string;
  recorded_at: string;
  date_label: string;
  place: string;
  place_entity_id: number | null;
  confidence: number;
  source_id: string;
  permalink: string;
  item_title: string;
  participants: UltraWikiEventParticipant[];
}

export interface UltraWikiPersonEventsPage {
  events: UltraWikiPersonEvent[];
  total: number;
  limit: number;
  offset: number;
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

const BASE = "/api/ultrawiki";

async function failure(response: Response): Promise<UltraWikiApiError> {
  let detail: unknown = null;
  try {
    const body = await response.json();
    detail = (body as { detail?: unknown })?.detail ?? body;
  } catch {
    detail = null;
  }
  // The backend's own sentence when it wrote one — a refusal like "undo merge
  // 12 first" is the entire value of the 409 and must reach the user intact.
  const message =
    typeof detail === "string" && detail.trim()
      ? detail
      : `HTTP ${response.status}`;
  return new UltraWikiApiError(message, response.status, detail);
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) throw await failure(response);
  return (await response.json()) as T;
}

async function postJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { method: "POST" });
  if (!response.ok) throw await failure(response);
  return (await response.json()) as T;
}

function queryString(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

// ---------------------------------------------------------------------------
// People
// ---------------------------------------------------------------------------

export async function fetchPeople(
  options: { q?: string; limit?: number; offset?: number } = {},
): Promise<UltraWikiPeoplePage> {
  return getJson<UltraWikiPeoplePage>(
    `${BASE}/identity/people${queryString({
      q: options.q,
      limit: options.limit,
      offset: options.offset,
    })}`,
  );
}

export async function fetchPerson(
  entityId: number,
): Promise<UltraWikiPersonAnswer> {
  return getJson<UltraWikiPersonAnswer>(
    `${BASE}/identity/people/${encodeURIComponent(String(entityId))}`,
  );
}

/** Import the address book as identity entities. Idempotent, re-runnable. */
export async function seedIdentities(): Promise<UltraWikiSeedAnswer> {
  return postJson<UltraWikiSeedAnswer>(`${BASE}/identity/seed`);
}

/**
 * The episodic events one person took part in.
 *
 * Served by the main UltraWiki route module rather than the identity one —
 * events are their own layer and the identity id is merely a filter on them.
 * A merged-away id still finds its events: the store forwards it.
 */
export async function fetchPersonEvents(
  entityId: number,
  options: { limit?: number } = {},
): Promise<UltraWikiPersonEventsPage> {
  return getJson<UltraWikiPersonEventsPage>(
    `${BASE}/events${queryString({
      entity_id: entityId,
      limit: options.limit ?? 25,
    })}`,
  );
}

// ---------------------------------------------------------------------------
// The confirmation queue
// ---------------------------------------------------------------------------

export async function fetchIdentityQueue(
  options: { status?: UltraWikiQueueFilter; limit?: number } = {},
): Promise<UltraWikiQueuePage> {
  return getJson<UltraWikiQueuePage>(
    `${BASE}/identity/queue${queryString({
      status: options.status,
      limit: options.limit,
    })}`,
  );
}

/** Apply one proposal. The answer's `merge_id` is what reverses it. */
export async function confirmIdentityMerge(
  queueId: number,
): Promise<UltraWikiConfirmAnswer> {
  return postJson<UltraWikiConfirmAnswer>(
    `${BASE}/identity/queue/${encodeURIComponent(String(queueId))}/confirm`,
  );
}

/** Decline one proposal — permanently. There is no un-reject route. */
export async function rejectIdentityMerge(
  queueId: number,
): Promise<UltraWikiRejectAnswer> {
  return postJson<UltraWikiRejectAnswer>(
    `${BASE}/identity/queue/${encodeURIComponent(String(queueId))}/reject`,
  );
}

// ---------------------------------------------------------------------------
// The merge audit trail + its undo
// ---------------------------------------------------------------------------

export async function fetchIdentityMerges(
  options: { entityId?: number; limit?: number } = {},
): Promise<UltraWikiMergeLogPage> {
  return getJson<UltraWikiMergeLogPage>(
    `${BASE}/identity/merges${queryString({
      entity_id: options.entityId,
      limit: options.limit,
    })}`,
  );
}

/** Undo one merge. Merges undo in reverse order; a shadowed one 409s. */
export async function unmergeIdentity(
  mergeId: number,
): Promise<UltraWikiUnmergeAnswer> {
  return postJson<UltraWikiUnmergeAnswer>(
    `${BASE}/identity/merges/${encodeURIComponent(String(mergeId))}/unmerge`,
  );
}

// ---------------------------------------------------------------------------
// Presentation helpers
// ---------------------------------------------------------------------------

/**
 * Split an evidence `kind` into the two i18n keys that render it.
 *
 * The backend emits a small family of kinds — `email`, `email_exact`,
 * `name_similar` — where the suffix says HOW it matched and the stem says
 * WHAT matched. Rendering the raw token ("name_similar") in a sentence a
 * non-developer reads is the thing this avoids, while an unknown stem still
 * degrades to the token itself rather than to a blank line.
 */
export function evidenceParts(kind: string): {
  phraseKey: string;
  nounKey: string | null;
  raw: string;
} {
  const token = String(kind || "").trim().toLowerCase();
  const similar = token.endsWith("_similar");
  const stem = token.replace(/_(exact|similar)$/, "");
  const known = (ULTRAWIKI_IDENTIFIER_KINDS as readonly string[]).includes(stem);
  return {
    phraseKey: similar
      ? "ultrawiki.people.evidence_similar"
      : "ultrawiki.people.evidence_same",
    nounKey: known ? `ultrawiki.people.noun_${stem}` : null,
    raw: token,
  };
}

/** The i18n key naming one match tier; unknown tiers fall back to the token. */
export function tierKey(tier: string): string | null {
  return (ULTRAWIKI_MATCH_TIERS as readonly string[]).includes(String(tier))
    ? `ultrawiki.people.tier_${tier}`
    : null;
}

/** A 0…1 score as a whole percentage — never "83.4711 %". */
export function scorePercent(score: number): number {
  if (!Number.isFinite(score)) return 0;
  return Math.round(Math.max(0, Math.min(1, score)) * 100);
}

/**
 * The merges of a person that still STAND and can therefore be undone.
 *
 * The audit trail keeps undone rows on purpose (a merge that was reversed is
 * still a fact about what happened), so "what can I undo here" is a filter,
 * never the raw list.
 */
export function undoableMerges(
  person: Pick<UltraWikiPerson, "merges">,
): UltraWikiMergeRecord[] {
  return (person.merges ?? []).filter((merge) => !merge.undone_at);
}

/** The human message behind a failed identity action. */
export function identityErrorMessage(error: unknown): string {
  if (error instanceof UltraWikiApiError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error ?? "");
}
