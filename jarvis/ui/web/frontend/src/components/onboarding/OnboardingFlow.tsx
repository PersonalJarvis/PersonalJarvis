import { useMemo, useState } from "react";
import { MascotGigi } from "@/components/MascotGigi";
import { useT } from "@/i18n";
import type { useOnboarding } from "@/hooks/useOnboarding";
import { WelcomeStep } from "./steps/WelcomeStep";
import { LanguageStep } from "./steps/LanguageStep";
import { PermissionsStep } from "./steps/PermissionsStep";
import { WakeWordStep } from "./steps/WakeWordStep";
import { ApiKeysStep } from "./steps/ApiKeysStep";
import { FinishStep } from "./steps/FinishStep";

export interface StepProps {
  onb: ReturnType<typeof useOnboarding>;
  goNext: () => void;
  goBack: () => void;
  skip: () => void;
  isFirst: boolean;
  isLast: boolean;
}

const REGISTRY: Record<string, (p: StepProps) => JSX.Element> = {
  welcome: WelcomeStep,
  language: LanguageStep,
  permissions: PermissionsStep,
  "wake-word": WakeWordStep,
  "api-keys": ApiKeysStep,
  finish: FinishStep,
};

// Exported for the cross-layer parity test: these must equal the backend's
// ONBOARDING_STEPS (jarvis/setup/onboarding_meta.py). A typo here would render
// the silent fallback div instead of a real step.
export const STEP_KEYS = Object.keys(REGISTRY);

export function OnboardingFlow({
  onb,
}: {
  onb: ReturnType<typeof useOnboarding>;
}) {
  const t = useT();
  const steps = onb.state?.steps ?? ["welcome", "finish"];
  // Always begin at the first step so every run walks each step in order. We do
  // NOT resume to a saved current_step: a user who already finished once would
  // otherwise be auto-jumped to the last step, which feels like the flow skipped
  // itself.
  const [idx, setIdx] = useState(0);
  const [skipped, setSkipped] = useState<string[]>(onb.state?.skipped_steps ?? []);

  const StepComp = useMemo(
    () => REGISTRY[steps[idx]] ?? ((_: StepProps) => <div>{steps[idx]}</div>),
    [steps, idx],
  );
  const isApiKeysGuide = steps[idx] === "api-keys";

  const advance = (next: number, nextSkipped = skipped) => {
    if (next >= steps.length) {
      void onb.complete();
      return;
    }
    setSkipped(nextSkipped);
    setIdx(next);
    void onb.saveStep(steps[next], nextSkipped);
  };

  const props: StepProps = {
    onb,
    goNext: () => advance(idx + 1),
    goBack: () => setIdx((i) => Math.max(0, i - 1)),
    skip: () => advance(idx + 1, [...new Set([...skipped, steps[idx]])]),
    isFirst: idx === 0,
    isLast: idx === steps.length - 1,
  };

  return (
    <div
      className={`flex max-h-[calc(100vh-2rem)] w-full flex-col gap-6 overflow-y-auto overscroll-contain rounded-2xl border border-border bg-card p-8 shadow-2xl scrollbar-jarvis ${
        isApiKeysGuide ? "max-w-4xl" : "max-w-lg"
      }`}
    >
      <div className="flex items-center justify-between">
        <div
          className="flex gap-1.5"
          role="progressbar"
          aria-label={t("onboarding.progress_label")}
          aria-valuemin={1}
          aria-valuemax={steps.length}
          aria-valuenow={idx + 1}
        >
          {steps.map((s, i) => (
            <span
              key={s}
              aria-hidden="true"
              className={`h-1.5 w-6 rounded-full ${i <= idx ? "bg-primary" : "bg-muted"}`}
            />
          ))}
        </div>
        <MascotGigi size={48} reactToVoice={false} enableComments={false} />
      </div>
      <StepComp {...props} />
      {!props.isFirst && (
        <button
          type="button"
          className="self-start touch-manipulation text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={props.goBack}
        >
          {t("onboarding.nav.back")}
        </button>
      )}
    </div>
  );
}
