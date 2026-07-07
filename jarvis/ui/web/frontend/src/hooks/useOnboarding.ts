import { useCallback, useEffect, useRef, useState } from "react";

export interface LegalReference {
  label: string;
  url: string;
}

export interface OnboardingState {
  completed: boolean;
  current_step: string | null;
  skipped_steps: string[];
  terms: { accepted: boolean; accepted_version: string | null; current_version: string };
  wake_word_acknowledged: boolean;
  legal_references: LegalReference[];
  steps: string[];
}

async function post(url: string, body?: unknown): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

// Bounded warmup retry: on a fresh machine the serve-first backend answers
// /api/onboarding/state from the fast-boot path immediately, but a slow disk
// or a dev server without it can still 503/refuse briefly. Retrying keeps the
// first-run gate from failing open (= never showing) on the one boot where it
// matters most. ~30 s total, then fail-open as before (never trap the user).
const RETRY_DELAYS_MS = [500, 1000, 1500, 2000, 3000, 3000, 4000, 5000, 5000, 5000];

/**
 * Loads /api/onboarding/state and exposes the onboarding mutation actions.
 * `saveStep` is best-effort (progress persistence — never blocks navigation);
 * `acceptTerms`/`acknowledgeWakeWord`/`complete` propagate failures so the
 * calling step can surface an error. `complete` dispatches
 * `jarvis:onboarding-changed` only after a successful POST.
 * `opts.retryDelaysMs` is a test seam for the warmup retry schedule.
 */
export function useOnboarding(opts?: { retryDelaysMs?: number[] }) {
  const [state, setState] = useState<OnboardingState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const retryDelays = opts?.retryDelaysMs ?? RETRY_DELAYS_MS;
  // Generation guard: a newer refetch (or unmount, which bumps it in the
  // effect cleanup) makes every older in-flight retry loop a no-op, so a
  // stale slow response can never clobber fresher state and no timer keeps
  // firing after the gate is gone.
  const genRef = useRef(0);

  const refetch = useCallback(async () => {
    const gen = ++genRef.current;
    setError(null);
    for (let attempt = 0; ; attempt++) {
      try {
        const res = await fetch("/api/onboarding/state");
        if (genRef.current !== gen) return; // superseded or unmounted
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        setState((await res.json()) as OnboardingState);
        setLoading(false);
        return;
      } catch (e) {
        if (genRef.current !== gen) return;
        const delay = retryDelays[attempt];
        if (delay === undefined) {
          setError((e as Error).message);
          setLoading(false);
          return;
        }
        await new Promise((r) => setTimeout(r, delay));
        if (genRef.current !== gen) return;
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- retryDelays is a stable test seam
  }, []);

  useEffect(() => {
    void refetch();
    return () => {
      genRef.current++; // invalidate in-flight retry loops on unmount
    };
  }, [refetch]);

  const saveStep = useCallback(async (step: string, skipped?: string[]) => {
    try {
      await post("/api/onboarding/step", { step, skipped });
    } catch {
      // best-effort progress persistence — never block navigation
    }
  }, []);

  const acceptTerms = useCallback(() => post("/api/onboarding/accept-terms"), []);
  const acknowledgeWakeWord = useCallback(
    () => post("/api/onboarding/acknowledge-wake-word"),
    [],
  );
  const complete = useCallback(async () => {
    await post("/api/onboarding/complete");
    window.dispatchEvent(new CustomEvent("jarvis:onboarding-changed"));
  }, []);

  return { state, loading, error, refetch, saveStep, acceptTerms, acknowledgeWakeWord, complete };
}
