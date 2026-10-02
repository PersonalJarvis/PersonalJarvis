import { useContext, type ReactNode } from "react";
import { QueryClientContext } from "@tanstack/react-query";

import { PetSprite } from "@/components/pets/PetSprite";
import { useActivePet } from "@/hooks/usePets";
import type { PetState } from "@/lib/petStates";
import { cn } from "@/lib/utils";
import { useEventStore, type VoiceState } from "@/store/events";

/**
 * Jarvis as the app draws it: the user's chosen pet (Settings -> My Pets),
 * animated from its own sprite sheet, at any size. With no pet chosen it is
 * Gigi, the built-in default. Every in-app spot that used to show the static
 * Gigi image (sidebar, greeting, composer chip, deck header, the live line of
 * a reasoning trace) draws this instead, so picking a pet changes them all
 * at once — `PetChanged` refetches the shared query.
 *
 * `state` fixes the animation row; `reactive` follows the voice instead
 * (listening while you talk, thinking, talking while it answers). Until the
 * pets answer arrives the box stays empty at its final size, so nothing
 * jumps and no stand-in image flashes.
 */
export function PetMark({
  size,
  state,
  reactive = false,
  label,
  className,
}: {
  size: number;
  state?: PetState;
  reactive?: boolean;
  /** Accessible name; decorative without one. */
  label?: string;
  className?: string;
}) {
  // Outside a query client (an isolated test render, a bare preview) there
  // is no pets answer to read; the empty box keeps the layout.
  const client = useContext(QueryClientContext);
  const box = (
    <span
      aria-hidden
      data-testid="pet-mark"
      className={cn("inline-block shrink-0", className)}
      style={{ width: size, height: size }}
    />
  );
  if (!client) return box;
  return <ActivePetMark size={size} state={state} reactive={reactive} label={label} className={className} fallback={box} />;
}

function ActivePetMark({
  size,
  state,
  reactive,
  label,
  className,
  fallback,
}: {
  size: number;
  state?: PetState;
  reactive: boolean;
  label?: string;
  className?: string;
  fallback: ReactNode;
}) {
  const pet = useActivePet();
  const voiceState = useEventStore((s) => s.voiceState);
  if (!pet) return <>{fallback}</>;
  const shown = state ?? (reactive ? petStateForVoice(voiceState) : "idle");
  return (
    <span data-testid="pet-mark" data-pet={pet.id} className={cn("inline-flex shrink-0 select-none", className)}>
      <PetSprite pet={pet} state={shown} px={size} label={label} />
    </span>
  );
}

/** The pet row that mirrors what the voice is doing right now. */
export function petStateForVoice(voice: VoiceState): PetState {
  switch (voice) {
    case "listening":
      return "listening";
    case "connecting":
    case "thinking":
      return "thinking";
    case "speaking":
      return "talking";
    case "error":
      return "error";
    case "paused":
      return "sleeping";
    default:
      return "idle";
  }
}
