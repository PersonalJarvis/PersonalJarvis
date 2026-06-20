import { useEffect, useMemo, useState } from "react";
import { useOnboarding } from "@/hooks/useOnboarding";
import { OnboardingFlow } from "./OnboardingFlow";
import { RiskGate } from "./RiskGate";

/**
 * Blocking overlay that shows the onboarding flow until it is completed.
 * Fails open (renders nothing) while loading or on a fetch error so a broken
 * guide never traps the user. Re-shows once when the accepted Terms version is
 * older than the shipped version (opening at the terms step). `?onboarding=force`
 * forces the flow for non-destructive dev replay.
 */
export function OnboardingGate() {
  const onb = useOnboarding();
  // Risk acknowledgement is gated in local state only — never persisted and
  // never touching onboarding/completed state, so it shows once per fresh open
  // of an unfinished guide and cannot reintroduce the restart-loop bug.
  const [riskAck, setRiskAck] = useState(false);

  useEffect(() => {
    const onChanged = () => void onb.refetch();
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

  const termsOutdated =
    onb.state.terms.accepted &&
    onb.state.terms.accepted_version !== onb.state.terms.current_version;
  const show = forced || !onb.state.completed || termsOutdated;
  if (!show) return null;

  // On a terms version bump for an already-completed install, re-open at terms.
  const initialStep = onb.state.completed && termsOutdated ? "terms" : undefined;

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/90 backdrop-blur-sm"
    >
      {riskAck ? (
        <OnboardingFlow onb={onb} initialStep={initialStep} />
      ) : (
        <RiskGate onAccept={() => setRiskAck(true)} />
      )}
    </div>
  );
}
