import { useEffect, useState } from "react";
import { MascotGigi } from "@/components/MascotGigi";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

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
 * Auto-advancing, captioned brand intro on the first step. The mascot and one
 * line of copy on the stage's own ground, framed by a hairline — not a boxed
 * video placeholder. Stops on the last scene; renders the final scene
 * immediately (no motion) when the user prefers reduced motion. Decorative —
 * the step owns the CTA below it.
 */
export function IntroSequence({ className }: { className?: string }) {
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
      className={cn(
        "relative flex flex-col items-center justify-center gap-5 border-y border-border/70 px-6 py-8",
        className,
      )}
      aria-live="polite"
    >
      <MascotGigi size={104} reactToVoice={false} enableComments={false} />
      <p
        key={scene}
        className="animate-in fade-in text-center text-[17px] font-medium leading-snug tracking-tight text-foreground [text-wrap:balance] motion-reduce:animate-none"
      >
        {t(SCENE_KEYS[scene])}
      </p>
      <div className="flex gap-1.5" aria-hidden>
        {SCENE_KEYS.map((k, i) => (
          <span
            key={k}
            className={cn(
              "h-1 w-5 rounded-full transition-colors",
              i <= scene ? "bg-foreground/70" : "bg-muted",
            )}
          />
        ))}
      </div>
    </div>
  );
}
