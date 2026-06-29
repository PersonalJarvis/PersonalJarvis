import { useState } from "react";
import { Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";

// The public onboarding walkthrough on YouTube. Embedded via the
// privacy-enhanced youtube-nocookie domain (no cookies until playback) with
// rel=0 so "related" videos stay within this channel.
//
// A lightweight click-to-play thumbnail facade is shown first: the heavy
// YouTube player iframe is only mounted once the user presses play. This makes
// the step render instantly — no black box and no visible "loading" flash while
// the embed boots — and defers the player module (and any YouTube contact beyond
// the single static thumbnail) until the user actually chooses to watch.
const VIDEO_ID = "FXz1HclXL1g";
const EMBED_SRC = `https://www.youtube-nocookie.com/embed/${VIDEO_ID}?rel=0&autoplay=1`;
const THUMB_SRC = `https://i.ytimg.com/vi/${VIDEO_ID}/maxresdefault.jpg`;
const THUMB_FALLBACK = `https://i.ytimg.com/vi/${VIDEO_ID}/hqdefault.jpg`;

/**
 * The onboarding tutorial video, shown as the second screen — right after the
 * RiskGate acknowledgement and before the step flow. Frontend-only and gated by
 * local state in OnboardingGate, so it never mutates onboarding/completed state
 * and cannot reintroduce the "onboarding reappears every restart" bug. Both the
 * primary button and the skip link simply advance to the flow, so a user who
 * does not want to watch is never blocked.
 *
 * The video uses a click-to-play thumbnail facade so the step appears instantly
 * instead of mounting (and visibly loading) the heavy YouTube iframe up front.
 */
export function IntroVideoScreen({ onContinue }: { onContinue: () => void }) {
  const t = useT();
  const [playing, setPlaying] = useState(false);

  return (
    <div className="flex w-full max-w-lg flex-col gap-5 rounded-2xl border border-border bg-card p-8 shadow-2xl">
      <div className="text-center">
        <h1 className="font-display text-xl font-semibold">{t("onboarding.tutorial.title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("onboarding.tutorial.body")}</p>
      </div>
      <div className="aspect-video w-full overflow-hidden rounded-xl border border-border bg-gradient-to-br from-background to-card">
        {playing ? (
          <iframe
            className="h-full w-full"
            src={EMBED_SRC}
            title={t("onboarding.tutorial.title")}
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
            allowFullScreen
          />
        ) : (
          <button
            type="button"
            onClick={() => setPlaying(true)}
            aria-label={t("onboarding.tutorial.play")}
            className="group relative block h-full w-full"
          >
            <img
              src={THUMB_SRC}
              alt=""
              aria-hidden="true"
              className="h-full w-full object-cover"
              onError={(e) => {
                const img = e.currentTarget;
                if (!img.src.endsWith("hqdefault.jpg")) img.src = THUMB_FALLBACK;
              }}
            />
            <span className="absolute inset-0 flex items-center justify-center bg-black/25 transition-colors group-hover:bg-black/15">
              <span className="flex h-16 w-16 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg transition-transform group-hover:scale-110">
                <Play className="h-7 w-7 translate-x-0.5 fill-current" />
              </span>
            </span>
          </button>
        )}
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
