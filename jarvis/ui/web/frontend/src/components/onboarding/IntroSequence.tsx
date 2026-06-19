import { useEffect, useState } from "react";
import { MascotGigi } from "@/components/MascotGigi";
import { useT } from "@/i18n";

const SCENE_KEYS = [
  "onboarding.intro.scene_1",
  "onboarding.intro.scene_2",
  "onboarding.intro.scene_3",
  "onboarding.intro.scene_4",
] as const;

const SCENE_MS = 2800;

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    // jsdom / older browsers without matchMedia — treat as "motion ok".
    return false;
  }
}

/**
 * Auto-advancing, captioned brand intro shown inside IntroClip when no video
 * asset is present. Stops on the last scene; renders the final scene immediately
 * (no motion) when the user prefers reduced motion. Decorative — the Welcome
 * step owns the CTA below it.
 */
export function IntroSequence() {
  const t = useT();
  const reduced = prefersReducedMotion();
  const [scene, setScene] = useState(reduced ? SCENE_KEYS.length - 1 : 0);

  useEffect(() => {
    if (reduced) return;
    const id = setInterval(() => {
      setScene((s) => (s < SCENE_KEYS.length - 1 ? s + 1 : s));
    }, SCENE_MS);
    return () => clearInterval(id);
  }, [reduced]);

  return (
    <div
      className="relative flex aspect-video w-full flex-col items-center justify-center gap-4 overflow-hidden rounded-xl border border-border bg-gradient-to-br from-background to-card"
      aria-live="polite"
    >
      <MascotGigi size={96} reactToVoice={false} enableComments={false} />
      <p
        key={scene}
        className="animate-in fade-in px-6 text-center text-sm font-medium text-foreground"
      >
        {t(SCENE_KEYS[scene])}
      </p>
      <div className="absolute bottom-3 flex gap-1.5" aria-hidden>
        {SCENE_KEYS.map((k, i) => (
          <span
            key={k}
            className={`h-1 w-5 rounded-full transition-colors ${i <= scene ? "bg-primary" : "bg-muted"}`}
          />
        ))}
      </div>
    </div>
  );
}
