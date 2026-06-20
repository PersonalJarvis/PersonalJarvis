import { useState } from "react";
import { IntroSequence } from "./IntroSequence";

/**
 * Plays the onboarding intro video when a usable `src` is given — autoplaying
 * muted (the video carries no audio) with controls so the user can pause or
 * scrub, and can simply move on with the Welcome CTA below to skip it. Falls
 * back to the animated IntroSequence when there is no src or the asset fails to
 * load, so a missing video never blocks onboarding.
 */
export function IntroClip({ src }: { src?: string }) {
  const [failed, setFailed] = useState(false);
  if (src && src.trim().length > 0 && !failed) {
    return (
      <video
        className="aspect-video w-full rounded-xl border border-border"
        src={src}
        controls
        autoPlay
        muted
        playsInline
        preload="metadata"
        onError={() => setFailed(true)}
      />
    );
  }
  return <IntroSequence />;
}
