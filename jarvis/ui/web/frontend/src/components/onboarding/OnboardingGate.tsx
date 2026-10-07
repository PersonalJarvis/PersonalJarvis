import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { useOnboarding } from "@/hooks/useOnboarding";
import { SETUP_REPLAY_EVENT, TOUR_START_EVENT } from "./tourEvents";

/**
 * Code-split: a finished install renders this gate as `null` forever, so the
 * setup and the walk load only on the boot that shows them — the gate's own
 * show/hide logic is all that rides in the entry chunk.
 */
const SetupTour = lazy(() =>
  import("./setup/SetupTour").then((m) => ({ default: m.SetupTour })),
);
const SectionWalk = lazy(() =>
  import("./setup/SectionWalk").then((m) => ({ default: m.SectionWalk })),
);

/** Where the IDE keeps working without app-wide setup — no guide over it. */
const IDE_SECTIONS = ["agentic-ide", "chat-workspace", "agentic-ide-classic"];

function param(name: string): string | null {
  return new URLSearchParams(window.location.search).get(name);
}

/**
 * First run, in two acts, both over the real app.
 *
 * 1. The setup window: three steps — the assistant's name (its wake word),
 *    connecting an AI (subscriptions and an API key), and how the user
 *    talks to it (macOS permissions are asked later, where a feature needs
 *    them; no step of setup asks for one). Fails open: while loading or on
 *    a fetch error the gate renders nothing, so a broken guide never traps
 *    anyone.
 * 2. The walk: the user's pet walks the app's sections and explains each in
 *    place. When it ends, onboarding is completed and the app restarts once.
 *
 * Settings' replay (`jarvis:setup-replay`) walks both again as a preview,
 * without completing or restarting anything. `jarvis:tour-start` (and an
 * install that finished setup but never saw the walk) shows the walk alone.
 *
 * Replay, never destructive: `?onboarding=force` walks the setup on a
 * finished install without completing or restarting anything; `?tour=force`
 * opens the walk alone.
 */
export function OnboardingGate({ activeSection }: { activeSection?: string } = {}) {
  const onb = useOnboarding();
  // Set once setup completes (jarvis:onboarding-changed) or a replay ends.
  const [dismissed, setDismissed] = useState(false);
  const [tourRequested, setTourRequested] = useState(() => param("tour") === "force");
  const [tourDone, setTourDone] = useState(false);
  // Bumped by each Settings replay; > 0 means a setup preview is requested,
  // and the value remounts the guide so every replay starts fresh.
  const [setupReplay, setSetupReplay] = useState(0);
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
    const onSetupReplay = () => {
      setDismissed(false);
      setTourDone(false);
      setTourRequested(false);
      setSetupReplay((n) => n + 1);
    };
    window.addEventListener("jarvis:onboarding-changed", onChanged);
    window.addEventListener(TOUR_START_EVENT, onTour);
    window.addEventListener(SETUP_REPLAY_EVENT, onSetupReplay);
    return () => {
      window.removeEventListener("jarvis:onboarding-changed", onChanged);
      window.removeEventListener(TOUR_START_EVENT, onTour);
      window.removeEventListener(SETUP_REPLAY_EVENT, onSetupReplay);
    };
  }, [onb]);

  const inIde = IDE_SECTIONS.includes(activeSection ?? "");

  if (onb.loading || onb.error || !onb.state) return null;

  const replaying = setupReplay > 0;
  const asked = forced || replaying;
  const isPreview = asked && onb.state.completed;
  const showSetup = (asked || !onb.state.completed) && !dismissed && (asked || !inIde);

  // A real first run is recorded as done (the walk included) and gets its
  // one completion restart; a replay just closes.
  const completeFirstRun = () => {
    void (async () => {
      await onb.completeTour();
      try {
        await onb.complete();
      } catch (e) {
        // The next start resumes setup at its last step, so nothing is lost.
        console.warn("onboarding: completing after the walk failed", e);
      }
    })();
  };

  if (showSetup) {
    return (
      <Suspense fallback={null}>
        <SetupTour
          key={setupReplay}
          onb={onb}
          preview={isPreview}
          onFinished={() => {
            setSetupReplay(0);
            setDismissed(true);
            if (isPreview) return;
            completeFirstRun();
          }}
        />
      </Suspense>
    );
  }

  const tourDue = tourRequested || (onb.state.completed && onb.state.tour_completed === false);
  // An automatic walk waits until the user is out of the IDE; a replay they
  // asked for starts wherever they are.
  if (tourDue && !tourDone && (tourRequested || !inIde)) {
    return (
      <Suspense fallback={null}>
        <SectionWalk
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
