import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";

// The public onboarding walkthrough on YouTube. Embedded via the
// privacy-enhanced youtube-nocookie domain (no cookies until playback) with
// rel=0 so "related" videos stay within this channel.
const VIDEO_ID = "FXz1HclXL1g";
const EMBED_SRC = `https://www.youtube-nocookie.com/embed/${VIDEO_ID}?rel=0`;

/**
 * The onboarding tutorial video, shown as the second screen — right after the
 * RiskGate acknowledgement and before the step flow. Frontend-only and gated by
 * local state in OnboardingGate, so it never mutates onboarding/completed state
 * and cannot reintroduce the "onboarding reappears every restart" bug. Both the
 * primary button and the skip link simply advance to the flow, so a user who
 * does not want to watch is never blocked.
 */
export function IntroVideoScreen({ onContinue }: { onContinue: () => void }) {
  const t = useT();
  return (
    <div className="flex w-full max-w-lg flex-col gap-5 rounded-2xl border border-border bg-card p-8 shadow-2xl">
      <div className="text-center">
        <h1 className="font-display text-xl font-semibold">{t("onboarding.tutorial.title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("onboarding.tutorial.body")}</p>
      </div>
      <div className="aspect-video w-full overflow-hidden rounded-xl border border-border bg-black">
        <iframe
          className="h-full w-full"
          src={EMBED_SRC}
          title={t("onboarding.tutorial.title")}
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
          allowFullScreen
        />
      </div>
      <Button className="w-full" onClick={onContinue}>
        {t("onboarding.tutorial.continue")}
      </Button>
      <button className="text-xs text-muted-foreground underline" onClick={onContinue}>
        {t("onboarding.tutorial.skip")}
      </button>
    </div>
  );
}
