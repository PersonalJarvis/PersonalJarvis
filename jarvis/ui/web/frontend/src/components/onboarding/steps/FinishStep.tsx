import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import type { StepProps } from "../OnboardingFlow";

export function FinishStep({ onb, goNext }: StepProps) {
  const t = useT();
  const skipped = onb.state?.skipped_steps ?? [];
  return (
    <div className="flex flex-col gap-4 text-center">
      <h2 className="font-display text-xl font-semibold">{t("onboarding.finish.title")}</h2>
      <p className="text-sm text-muted-foreground">{t("onboarding.finish.body")}</p>
      {skipped.length > 0 && (
        <div className="text-xs text-muted-foreground">
          <div className="font-medium">{t("onboarding.finish.skipped_title")}</div>
          <ul className="mt-1">
            {skipped.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex items-start gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-left text-xs text-muted-foreground">
        <span aria-hidden className="mt-px text-primary">⏱</span>
        <span>{t("onboarding.finish.boot_notice")}</span>
      </div>
      <Button className="w-full" onClick={goNext}>{t("onboarding.finish.start_cta")}</Button>
    </div>
  );
}
