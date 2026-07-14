import { CheckCircle2, KeyRound, Radio, Waypoints } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import type { StepProps } from "../OnboardingFlow";

const GUIDE_SCREENSHOT = "/onboarding/api-keys-realtime-guide.png";

/**
 * Introduces the credential path without embedding the full provider console in
 * a first-run modal. The screenshot is captured from the real API Keys view;
 * percentage-based markers stay aligned when the image scales down.
 */
export function ApiKeysStep({ goNext }: StepProps) {
  const t = useT();

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
          <KeyRound aria-hidden="true" className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <h2 className="mb-1 font-display text-xl font-semibold text-balance">
            {t("onboarding.api_keys.title")}
          </h2>
          <p className="text-sm leading-relaxed text-muted-foreground">
            {t("onboarding.api_keys.body")}
          </p>
        </div>
      </div>

      <figure className="overflow-hidden rounded-xl border border-border bg-background shadow-sm">
        <div className="relative aspect-[1.96/1] overflow-hidden bg-muted">
          <img
            src={GUIDE_SCREENSHOT}
            alt={t("onboarding.api_keys.screenshot_alt")}
            width={2048}
            height={1040}
            className="h-full w-full object-cover"
            decoding="async"
            draggable={false}
          />

          <div
            data-testid="api-keys-marker"
            aria-hidden="true"
            className="pointer-events-none absolute left-[0.3%] top-[48.7%] h-[5.6%] w-[10.7%] rounded-md border-2 border-primary bg-primary/10 shadow-[0_0_0_3px_rgba(231,196,110,0.18)]"
          />
          <span
            aria-hidden="true"
            className="pointer-events-none absolute left-[10.2%] top-[47.2%] flex h-6 w-6 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground shadow-lg"
          >
            1
          </span>

          <div
            data-testid="voice-mode-marker"
            aria-hidden="true"
            className="pointer-events-none absolute left-[83.4%] top-[8.2%] h-[6.2%] w-[15.8%] rounded-md border-2 border-primary bg-primary/10 shadow-[0_0_0_3px_rgba(231,196,110,0.18)]"
          />
          <span
            aria-hidden="true"
            className="pointer-events-none absolute left-[82.6%] top-[6.7%] flex h-6 w-6 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground shadow-lg"
          >
            2
          </span>
        </div>
      </figure>

      <div className="grid gap-3 sm:grid-cols-2">
        <section className="rounded-xl border border-border bg-muted/30 p-4">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
              1
            </span>
            {t("onboarding.api_keys.api_location_title")}
          </div>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("onboarding.api_keys.api_location_body")}
          </p>
        </section>

        <section className="rounded-xl border border-primary/40 bg-primary/5 p-4">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
              2
            </span>
            {t("onboarding.api_keys.voice_mode_title")}
          </div>
          <div className="space-y-2 text-xs leading-relaxed text-muted-foreground">
            <p className="flex items-start gap-2">
              <Waypoints aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
              <span>
                <strong className="font-semibold text-foreground">
                  {t("onboarding.api_keys.pipeline_label")}
                </strong>{" "}
                {t("onboarding.api_keys.pipeline_body")}
              </span>
            </p>
            <p className="flex items-start gap-2">
              <Radio aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
              <span>
                <strong className="font-semibold text-foreground">
                  {t("onboarding.api_keys.realtime_label")}
                </strong>{" "}
                {t("onboarding.api_keys.realtime_body")}
              </span>
            </p>
          </div>
        </section>
      </div>

      <p className="flex items-start gap-2 rounded-lg bg-emerald-500/10 px-3 py-2.5 text-xs leading-relaxed text-emerald-600">
        <CheckCircle2 aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
        {t("onboarding.api_keys.security_note")}
      </p>

      <Button className="w-full" onClick={goNext}>
        {t("onboarding.api_keys.continue")}
      </Button>
    </div>
  );
}
