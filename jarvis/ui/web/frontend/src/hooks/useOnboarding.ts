import { useCallback, useEffect, useState } from "react";

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

/**
 * Loads /api/onboarding/state and exposes the onboarding mutation actions.
 * `saveStep` is best-effort (progress persistence — never blocks navigation);
 * `acceptTerms`/`acknowledgeWakeWord`/`complete` propagate failures so the
 * calling step can surface an error. `complete` dispatches
 * `jarvis:onboarding-changed` only after a successful POST.
 */
export function useOnboarding() {
  const [state, setState] = useState<OnboardingState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/onboarding/state");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setState((await res.json()) as OnboardingState);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
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
