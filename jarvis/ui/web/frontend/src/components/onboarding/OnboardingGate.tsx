import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { useOnboarding } from "@/hooks/useOnboarding";
import { TOUR_START_EVENT } from "./tourEvents";

/**
 * Code-split: a finished install renders this gate as `null` forever, so the
 * setup and the tour load only on the boot that shows them — the gate's own
 * show/hide logic is all that rides in the entry chunk.
 */
const SetupTour = lazy(() =>
  import("./setup/SetupTour").then((m) => ({ default: m.SetupTour })),
);
const GuidedTour = lazy(() =>
  import("./tour/GuidedTour").then((m) => ({ default: m.GuidedTour })),
);

/** Where the IDE keeps working without app-wide setup — no guide over it. */
const IDE_SECTIONS = ["agentic-ide", "chat-workspace", "agentic-ide-classic"];

function param(name: string): string | null {
  return new URLSearchParams(window.location.search).get(name);
}

/**
 * First run, in two acts, both on the real app — there is no setup screen.
 *
 * 1. Setup: the window dims and a guide walks the user to the app's own
 *    places — consent, the API Keys page, the wake word in Settings (and the
 *    macOS permissions) — until onboarding is complete. Fails open: while
 *    loading or on a fetch error it renders nothing, so a broken guide never
 *    traps anyone.
 * 2. The tour: after the completion restart, a spotlight explains the app
 *    one control at a time. Shown once (`tour_completed`), replayable from
 *    Settings via `jarvis:tour-start`.
 *
 * Replay, never destructive: `?onboarding=force` walks the setup on a
 * finished install without completing or restarting anything, then hands
 * over to the tour; `?tour=force` opens the tour alone.
 */
export function OnboardingGate({ activeSection }: { activeSection?: string } = {}) {
  const onb = useOnboarding();
  // Set once setup completes (the Start action dispatches
  // jarvis:onboarding-changed) or a replay ends.
  const [dismissed, setDismissed] = useState(false);
  const [tourRequested, setTourRequested] = useState(() => param("tour") === "force");
  const [tourDone, setTourDone] = useState(false);
  const forced = useMemo(() => param("onboarding") === "force", []);

  useEffect(() => {
    const onChanged = () => {
      void onb.refetch();
      setDismissed(true);
    };
    const onTour = () => {
      setTourDone(false);
      setTourRequested(true);
    };
    window.addEventListener("jarvis:onboarding-changed", onChanged);
    window.addEventListener(TOUR_START_EVENT, onTour);
    return () => {
      window.removeEventListener("jarvis:onboarding-changed", onChanged);
      window.removeEventListener(TOUR_START_EVENT, onTour);
    };
  }, [onb]);

  const inIde = IDE_SECTIONS.includes(activeSection ?? "");

  if (onb.loading || onb.error || !onb.state) return null;

  const showSetup = (forced || !onb.state.completed) && !dismissed && (forced || !inIde);
  if (showSetup) {
    return (
      <Suspense fallback={null}>
        <SetupTour
          onb={onb}
          preview={forced && onb.state.completed}
          onFinished={() => {
            setDismissed(true);
            setTourDone(false);
            setTourRequested(true);
          }}
        />
      </Suspense>
    );
  }

  const tourDue = tourRequested || (onb.state.completed && onb.state.tour_completed === false);
  // An automatic tour waits until the user is out of the IDE; a replay they
  // asked for starts wherever they are.
  if (tourDue && !tourDone && (tourRequested || !inIde)) {
    return (
      <Suspense fallback={null}>
        <GuidedTour
          onDone={() => {
            setTourDone(true);
            setTourRequested(false);
            void onb.completeTour();
          }}
        />
      </Suspense>
    );
  }

  return null;
}
