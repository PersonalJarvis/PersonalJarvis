import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import type { StepProps } from "../OnboardingFlow";
import { IntroClip } from "../IntroClip";

export function WelcomeStep({ goNext, skip }: StepProps) {
  const t = useT();
  return (
    <div className="flex flex-col gap-5 text-center">
      <h1 className="font-display text-2xl font-semibold">{t("onboarding.welcome.title")}</h1>
      <p className="text-sm text-muted-foreground">{t("onboarding.welcome.subtitle")}</p>
      <IntroClip />
      <Button className="w-full" onClick={goNext}>{t("onboarding.welcome.cta")}</Button>
      <button className="text-xs text-muted-foreground underline" onClick={skip}>
        {t("onboarding.welcome.skip_setup")}
      </button>
    </div>
  );
}
