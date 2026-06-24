import { useMemo, useState } from "react";
import { MascotGigi } from "@/components/MascotGigi";
import { useT } from "@/i18n";
import type { useOnboarding } from "@/hooks/useOnboarding";
import { WelcomeStep } from "./steps/WelcomeStep";
import { TermsStep } from "./steps/TermsStep";
import { LanguageStep } from "./steps/LanguageStep";
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
  terms: TermsStep,
  language: LanguageStep,
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
  initialStep,
}: {
  onb: ReturnType<typeof useOnboarding>;
  initialStep?: string;
}) {
  const t = useT();
  const steps = onb.state?.steps ?? ["welcome", "finish"];
  const initialIdx = Math.max(0, steps.indexOf(initialStep ?? onb.state?.current_step ?? "welcome"));
  const [idx, setIdx] = useState(initialIdx);
  const [skipped, setSkipped] = useState<string[]>(onb.state?.skipped_steps ?? []);

  const StepComp = useMemo(
    () => REGISTRY[steps[idx]] ?? ((_: StepProps) => <div>{steps[idx]}</div>),
    [steps, idx],
  );

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
    <div className="flex w-full max-w-lg flex-col gap-6 rounded-2xl border border-border bg-card p-8 shadow-2xl">
      <div className="flex items-center justify-between">
        <div className="flex gap-1.5">
          {steps.map((s, i) => (
            <span
              key={s}
              className={`h-1.5 w-6 rounded-full ${i <= idx ? "bg-primary" : "bg-muted"}`}
            />
          ))}
        </div>
        <MascotGigi size={48} reactToVoice={false} enableComments={false} />
      </div>
      <StepComp {...props} />
      {!props.isFirst && (
        <button
          className="self-start text-xs text-muted-foreground underline"
          onClick={props.goBack}
        >
          {t("onboarding.nav.back")}
        </button>
      )}
    </div>
  );
}
