/**
 * The Profile view's data layer — the shapes `profile_routes.py` returns, one
 * fetch helper, and the shared field mutation.
 *
 * The one subtlety worth keeping: a 503 from the profile endpoints is NOT a
 * failure. The legacy Curator is soft-disabled by design, and the backend
 * contract says the UI renders that as a calm, explained state rather than a
 * red badge — so the status code is carried on the Error and every caller can
 * tell "intentionally off" from "actually broken".
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { useEventStore } from "@/store/events";
import type { ClusterId } from "@/views/profile/ledger";

// ----------------------------------------------------------------------
// Response shapes — mirror jarvis/ui/web/profile_routes.py
// ----------------------------------------------------------------------

export interface PersonSummary {
  name: string;
  relationship: string;
  aliases: string[];
  slug: string;
}

export interface ProfileResponse {
  user: {
    name: string | null;
    meta: Record<string, unknown>;
    path: string;
  };
  people: PersonSummary[];
  reviews_count: number;
  has_avatar?: boolean;
}

export interface ReviewCandidate {
  idx: number;
  subject: string;
  is_person: boolean;
  person_name: string | null;
  cluster: string;
  field: string;
  value: unknown;
  operation: string;
  confidence: number;
  evidence: string;
  relationship: string | null;
  reason: string;
}

export interface ReviewsResponse {
  reviews: ReviewCandidate[];
  total: number;
}

export interface RawProfileResponse {
  content: string;
  path: string;
  mtime_ms: number | null;
  size_bytes: number;
}

/** An Error that carries the HTTP status, so a 503 can be told apart. */
export type HttpError = Error & { status?: number };

/** The status a caller can read off any error thrown by `fetchJson`. */
export function statusOf(error: unknown): number | undefined {
  return (error as HttpError | null)?.status;
}

// ----------------------------------------------------------------------
// Fetching
// ----------------------------------------------------------------------

export async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    if (res.status === 503) {
      const data = await res.json().catch(() => ({ detail: "Profile system not ready." }));
      const err = new Error(data.detail ?? `HTTP ${res.status}`) as HttpError;
      err.status = 503;
      throw err;
    }
    const txt = await res.text().catch(() => "");
    const err = new Error(`HTTP ${res.status}: ${txt || res.statusText}`) as HttpError;
    err.status = res.status;
    throw err;
  }
  return res.json();
}

// ----------------------------------------------------------------------
// Field editing
// ----------------------------------------------------------------------

export type FieldOp = "set" | "clear" | "append" | "remove";

export interface FieldEditBody {
  cluster: ClusterId;
  field: string;
  operation: FieldOp;
  value?: unknown;
}

/**
 * PATCH /api/profile/field. Invalidates the profile query on success so the
 * ledger re-renders from the persisted value rather than from an optimistic
 * guess — the file on disk is the source of truth here, not this component.
 */
export function useFieldEdit() {
  const queryClient = useQueryClient();
  const pushToast = useEventStore((s) => s.pushToast);
  return useMutation({
    mutationFn: async (body: FieldEditBody) => {
      const res = await fetch("/api/profile/field", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(data.detail ?? `HTTP ${res.status}`);
      }
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["profile"] }),
    onError: (err: Error) => pushToast("error", err.message),
  });
}

// ----------------------------------------------------------------------
// Display helpers
// ----------------------------------------------------------------------

/** One field value as a readable string; booleans go through the locale. */
export function renderValue(t: (key: string) => string, value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join(" · ");
  if (typeof value === "boolean") {
    return value ? t("profile_view.value_yes") : t("profile_view.value_no");
  }
  return String(value ?? "");
}

/** The sub-object one cluster keeps inside USER.md's front matter. */
export function clusterDataOf(
  meta: Record<string, unknown>,
  cluster: ClusterId,
): Record<string, unknown> {
  const raw = meta[cluster];
  return raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
}
