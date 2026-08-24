import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { useOnboarding } from "@/hooks/useOnboarding";

/**
 * Code-split: a completed install renders this gate exactly once per mount —
 * a no-op `null` return, forever — yet the full six-step flow (with its own
 * step components) used to travel in the entry chunk anyway, on every boot,
 * for every returning user. Split the same way MainView.tsx splits section
 * views: only the gate's own show/hide logic stays static, the flow loads on
 * the one boot that actually shows it.
 */
const OnboardingFlow = lazy(() =>
  import("./OnboardingFlow").then((m) => ({ default: m.OnboardingFlow })),
);

/**
 * The first-run stage. It covers the whole window on the app's own ground —
 * no scrim, no blur, no floating card: on a fresh install there is nothing
 * behind it worth hinting at, and a dialog over a half-built app looked
 * exactly like what it was. Fails open (renders nothing) while loading or on
 * a fetch error so a broken guide never traps the user. `?onboarding=force`
 * forces the flow for non-destructive dev replay.
 *
 * The risk acknowledgement lives INSIDE the flow's first step now (it used to
 * be a separate screen before a separate video screen before the wizard).
 * Its acceptance is awaited and persisted there; nothing about
 * onboarding/completed state changes until the final step, so the
 * restart-loop bug cannot come back through this path.
 */
export function OnboardingGate() {
  const onb = useOnboarding();
  // Set once the user completes the guide (the "Start" / complete() path
  // dispatches jarvis:onboarding-changed). It dismisses the stage even under
  // ?onboarding=force, so a dev replay closes on finish exactly like a real
  // first run instead of staying stuck open.
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    const onChanged = () => {
      void onb.refetch();
      setDismissed(true);
    };
    window.addEventListener("jarvis:onboarding-changed", onChanged);
    return () => window.removeEventListener("jarvis:onboarding-changed", onChanged);
  }, [onb]);

  const forced = useMemo(
    () => new URLSearchParams(window.location.search).get("onboarding") === "force",
    [],
  );

  if (onb.loading) return null;
  if (onb.error) return null; // fail open — never trap the user
  if (!onb.state) return null;

  const show = (forced || !onb.state.completed) && !dismissed;
  if (!show) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 bg-background text-foreground"
    >
      {/* Fallback is empty: the stage ground is already painted while the
          first-run chunk fetches. */}
      <Suspense fallback={null}>
        <OnboardingFlow onb={onb} />
      </Suspense>
    </div>
  );
}
