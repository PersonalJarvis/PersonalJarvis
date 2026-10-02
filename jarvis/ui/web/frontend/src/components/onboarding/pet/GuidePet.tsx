/**
 * The pet that guides a new user: the user's own desktop pet (Settings → My
 * Pets), drawn from its sprite sheet, speaking in a bubble.
 *
 * Used by every guide surface — the "how it works" walk, the setup steps,
 * the app tour and the first-steps guide — so one character explains the
 * whole first run. While a line is being "said" the text types itself out
 * and the pet plays its talking row; then it settles into the beat's own
 * state (thinking, working, success …). A click on the bubble finishes the
 * line at once; reduced motion shows it whole.
 *
 * The pet comes from `/api/pets` (read once per window). With no pet chosen
 * the built-in default stands in; without the endpoint (an older backend,
 * a test) the Gigi mascot does.
 */
import { useReducedMotion } from "framer-motion";
import { useEffect, useState, type ReactNode } from "react";
import { MascotGigi } from "@/components/MascotGigi";
import { PetSprite } from "@/components/pets/PetSprite";
import { NO_PET_ID, type PetState } from "@/lib/petStates";
import type { Pet } from "@/lib/petsApi";
import { cn } from "@/lib/utils";

/** The pet shown when none is chosen. */
const DEFAULT_PET_ID = "gigi";
/** Typing speed of a line, ms per character. */
const TYPE_MS = 18;

let cached: Promise<Pet | null> | null = null;

function loadGuidePet(): Promise<Pet | null> {
  cached ??= (async () => {
    try {
      const res = await fetch("/api/pets");
      if (!res.ok) return null;
      const data = (await res.json()) as { active?: string; pets?: Pet[] };
      const pets = Array.isArray(data.pets) ? data.pets : [];
      const id = data.active && data.active !== NO_PET_ID ? data.active : DEFAULT_PET_ID;
      return pets.find((p) => p.id === id) ?? pets.find((p) => p.id === DEFAULT_PET_ID) ?? pets[0] ?? null;
    } catch {
      // No pets endpoint: the mascot stands in.
      return null;
    }
  })();
  return cached;
}

/** The user's pet, or null while loading / when there is none. */
export function useGuidePet(): Pet | null {
  const [pet, setPet] = useState<Pet | null>(null);
  useEffect(() => {
    let cancelled = false;
    void loadGuidePet().then((p) => {
      if (!cancelled) setPet(p);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return pet;
}

/** Test seam: forget the cached pet. */
export function _resetGuidePetForTests(): void {
  cached = null;
}

/** Reveals `text` character by character; `done` once it is all there. */
export function useTypedText(text: string): { shown: string; done: boolean; finish: () => void } {
  const reduced = useReducedMotion() ?? false;
  const [count, setCount] = useState(reduced ? text.length : 0);
  useEffect(() => {
    if (reduced) {
      setCount(text.length);
      return;
    }
    setCount(0);
    let n = 0;
    const timer = window.setInterval(() => {
      n += 2;
      setCount(Math.min(n, text.length));
      if (n >= text.length) window.clearInterval(timer);
    }, TYPE_MS * 2);
    return () => window.clearInterval(timer);
  }, [text, reduced]);
  return { shown: text.slice(0, count), done: count >= text.length, finish: () => setCount(text.length) };
}

/** The pet itself at `px` size, or the mascot when there is no pet. */
export function GuidePetFigure({ state, px = 96, className }: { state: PetState; px?: number; className?: string }) {
  const pet = useGuidePet();
  if (!pet) {
    return (
      <div className={cn("flex shrink-0 items-end justify-center", className)} style={{ width: px, height: px }}>
        <MascotGigi size={Math.round(px * 0.7)} reactToVoice={false} enableComments={false} />
      </div>
    );
  }
  return (
    <PetSprite
      pet={pet}
      state={state}
      scale={Math.max(1, Math.floor(px / pet.frame_size))}
      label={pet.name}
      className={className}
    />
  );
}

/**
 * The pet next to a speech bubble saying `text`. `children` (the actions)
 * appear under the line once it is fully typed, so a button never jumps.
 */
export function PetSays({
  text,
  state = "idle",
  heading,
  children,
  px = 96,
  testId,
}: {
  text: string;
  state?: PetState;
  heading?: ReactNode;
  children?: ReactNode;
  px?: number;
  testId?: string;
}) {
  const { shown, done, finish } = useTypedText(text);
  return (
    <div className="flex items-end gap-2" data-testid={testId}>
      <GuidePetFigure state={done ? state : "talking"} px={px} className="-mb-1" />
      <div
        className="relative min-w-0 flex-1 rounded-2xl rounded-bl-md border border-border bg-popover px-4 py-3 text-popover-foreground shadow-float"
        onClick={done ? undefined : finish}
      >
        {/* The bubble's tail, pointing at the pet. */}
        <span
          aria-hidden
          className="absolute -left-[7px] bottom-3 h-3 w-3 rotate-45 border-b border-l border-border bg-popover"
        />
        {heading}
        {/* The full line is laid out invisibly underneath, so the bubble
            has its final size from the first letter on. */}
        <p className="relative text-[15px] leading-relaxed text-foreground">
          <span aria-hidden className="invisible">{text}</span>
          <span className="absolute inset-0" aria-live="polite" data-testid={testId ? `${testId}-text` : undefined}>
            {shown}
          </span>
        </p>
        {children && <div className={cn("transition-opacity duration-200", done ? "opacity-100" : "pointer-events-none opacity-0")}>{children}</div>}
      </div>
    </div>
  );
}
