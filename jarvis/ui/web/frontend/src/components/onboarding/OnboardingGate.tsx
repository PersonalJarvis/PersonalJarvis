import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { useOnboarding } from "@/hooks/useOnboarding";
import { TOUR_START_EVENT } from "./tourEvents";

/**
 * Code-split: a finished install renders this gate as `null` forever, so the
 * guide and the tour load only on the boot that shows them — the gate's own
 * show/hide logic is all that rides in the entry chunk.
 */
const WelcomeStage = lazy(() =>
  import("./WelcomeFlow").then((m) => ({ default: m.WelcomeStage })),
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
 * First run, in two acts.
 *
 * 1. The guide: one card on the app's own ground (the caption bar stays free)
 *    until setup is complete. Fails open — while loading or on a fetch error
 *    it renders nothing, so a broken guide never traps anyone.
 * 2. The tour: after the completion restart, the real app with a spotlight on
 *    one control at a time. Shown once (`tour_completed`), replayable from
 *    Settings via `jarvis:tour-start`.
 *
 * Dev replay, both non-destructive until the final action: `?onboarding=force`
 * opens the guide, `?tour=force` the tour.
 */
export function OnboardingGate({ activeSection }: { activeSection?: string } = {}) {
  const onb = useOnboarding();
  // Set once the guide completes (the Start action dispatches
  // jarvis:onboarding-changed). Closes the stage even under ?onboarding=force.
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

  const showGuide = (forced || !onb.state.completed) && !dismissed && (forced || !inIde);
  if (showGuide) {
    return (
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="onboarding-title"
        className="fixed inset-x-0 bottom-0 top-8 z-50 overflow-y-auto bg-background text-foreground scrollbar-jarvis"
      >
        {/* No fallback: the ground is already painted while the chunk loads. */}
        <Suspense fallback={null}>
          <WelcomeStage onb={onb} />
        </Suspense>
      </div>
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
