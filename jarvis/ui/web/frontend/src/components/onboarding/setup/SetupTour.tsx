import { useCallback, useState } from "react";
import type { useOnboarding } from "@/hooks/useOnboarding";
import { useLocaleChunk } from "@/i18n";
import { SectionWalk } from "./SectionWalk";
import { SetupWizard } from "./SetupWizard";
import { isWizardStep, resumeStep, type SetupStepId } from "./setupSteps";

type Onb = ReturnType<typeof useOnboarding>;

/**
 * First-run setup, in two acts.
 *
 * First the setup window: three steps in one dialog — the name (which is the
 * wake word), connecting an AI (subscriptions and an API key), and how the
 * user talks to it. Then the walk: the user's pet walks the real app and
 * explains each section in place.
 *
 * The gate completes onboarding (and restarts the app once) when the walk
 * ends. `preview` (a replay from Settings) never writes the onboarding state —
 * the user's own changes on the steps (a name, a key, a sign-in) still save,
 * because they are the user's actions, not the guide's.
 */
export function SetupTour({
  onb,
  preview,
  onFinished,
  startAt,
}: {
  onb: Onb;
  preview: boolean;
  onFinished: () => void;
  startAt?: SetupStepId;
}) {
  const ready = useLocaleChunk("onboarding");
  // A replay shows every step from the start; a real first run resumes where
  // it left off.
  const [stepId, setStepId] = useState<SetupStepId>(() =>
    preview ? (startAt ?? "name") : resumeStep(onb.state?.current_step ?? null),
  );
  const [skipped, setSkipped] = useState<string[]>(() => onb.state?.skipped_steps ?? []);

  const goTo = useCallback(
    (target: SetupStepId, nextSkipped: string[]) => {
      setStepId(target);
      if (!preview) void onb.saveStep(target, nextSkipped);
    },
    [onb, preview],
  );

  const skipTo = useCallback(
    (target: SetupStepId) => {
      const nextSkipped = skipped.includes(stepId) ? skipped : [...skipped, stepId];
      setSkipped(nextSkipped);
      goTo(target, nextSkipped);
    },
    [skipped, stepId, goTo],
  );

  if (!ready) return null;

  if (!isWizardStep(stepId)) return <SectionWalk onDone={onFinished} />;

  return (
    <SetupWizard
      step={stepId}
      preview={preview}
      onStep={(target) => goTo(target, skipped.filter((id) => id !== stepId))}
      onSkip={skipTo}
      onFinish={() => goTo("tour", skipped)}
    />
  );
}
