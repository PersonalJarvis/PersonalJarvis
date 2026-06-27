import { useEffect, useMemo, useState } from "react";
import { useOnboarding } from "@/hooks/useOnboarding";
import { OnboardingFlow } from "./OnboardingFlow";
import { RiskGate } from "./RiskGate";
import { IntroVideoScreen } from "./IntroVideoScreen";

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
  // The tutorial video is the second screen — shown after the risk gate and
  // before the step flow. Local-state only (like riskAck), so it never touches
  // onboarding/completed state and re-shows on a fresh open / ?onboarding=force
  // replay. A null mutation guarantees it cannot reintroduce the restart-loop.
  const [videoSeen, setVideoSeen] = useState(false);
  // Set once the user completes the guide (the "Get started" / complete() path
  // dispatches jarvis:onboarding-changed). It dismisses the overlay even under
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

  const termsOutdated =
    onb.state.terms.accepted &&
    onb.state.terms.accepted_version !== onb.state.terms.current_version;
  const show = (forced || !onb.state.completed || termsOutdated) && !dismissed;
  if (!show) return null;

  // On a terms version bump for an already-completed install, re-open at terms.
  const isTermsBumpReopen = Boolean(onb.state.completed && termsOutdated);
  const initialStep = isTermsBumpReopen ? "terms" : undefined;
  // Skip the tutorial on a pure terms-bump re-open: that user has already seen
  // it and is only re-accepting a new Terms version.
  const showVideo = !videoSeen && !isTermsBumpReopen;

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/90 backdrop-blur-sm"
    >
      {!riskAck ? (
        <RiskGate onAccept={() => setRiskAck(true)} />
      ) : showVideo ? (
        <IntroVideoScreen onContinue={() => setVideoSeen(true)} />
      ) : (
        <OnboardingFlow onb={onb} initialStep={initialStep} />
      )}
    </div>
  );
}
