import { create } from "zustand";

import type { ProviderTestResult } from "@/hooks/useProviders";

/**
 * The last live test each provider card ran THIS session.
 *
 * `configured` in `/api/providers` is a presence check — "a key string is
 * stored" — and a row that called that "ready" claimed more than it knew
 * (maintainer report 2026-08-23: four cards read ready, none could answer).
 * The honest vocabulary therefore needs to know whether a provider has been
 * verified, and the only verification the app has is the card's own Test
 * button (plus the section-health rollup, which covers the ACTIVE provider of
 * each tier). Results live here, keyed by provider id, so the row's state
 * chip can read what the footer found without the two being the same
 * component. Session-scoped on purpose: a key that worked yesterday is not
 * known to work today, and a stale green would be the old lie again.
 */
interface ProviderTestStore {
  results: Record<string, ProviderTestResult>;
  record: (providerId: string, result: ProviderTestResult) => void;
  forget: (providerId: string) => void;
}

export const useProviderTestStore = create<ProviderTestStore>((set) => ({
  results: {},
  record: (providerId, result) =>
    set((s) => ({ results: { ...s.results, [providerId]: result } })),
  forget: (providerId) =>
    set((s) => {
      if (!(providerId in s.results)) return s;
      const next = { ...s.results };
      delete next[providerId];
      return { results: next };
    }),
}));

/** What a stored test result says about the provider, for the state chip. */
export type Verification = "ok" | "failed" | null;

export function verificationOf(result: ProviderTestResult | undefined): Verification {
  if (!result) return null;
  if (result.status === "ok") return "ok";
  // "not configured" is not a verdict on the key — there was none to test.
  if (result.status === "not_configured") return null;
  return "failed";
}
