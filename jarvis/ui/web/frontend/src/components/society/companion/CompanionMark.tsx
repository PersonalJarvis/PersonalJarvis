import { useContext } from "react";
import { QueryClientContext } from "@tanstack/react-query";

import { PetSprite } from "@/components/pets/PetSprite";
import { PetStageContext } from "@/components/pets/petStage";
import { usePetById } from "@/hooks/usePets";
import type { PetState } from "@/lib/petStates";
import { AgentSymbol } from "../AgentSymbol";
import type { CompanionAppearance } from "./appearance";

type MarkProps = {
  appearance: CompanionAppearance;
  size: number;
  /** The agent is at work: the shape shows thinking dots, a pet plays its working row. */
  thinking?: boolean;
  /** Eyes a stage can animate (shapes only; a pet acts through its own rows). */
  expressive?: boolean;
  /** Fixes the pet's row; otherwise a surrounding stage or `thinking` decides. */
  state?: PetState;
};

/**
 * An agent's face in 2D: its shape symbol, or the pet it wears instead
 * (`companion.pet`, chosen in the look dialog from My Pets). Every surface
 * that draws an agent goes through here, so choosing a pet changes the
 * roster, the chats, the bot stage and the level hall at once.
 *
 * A pet face reads the shared pets answer. Until it arrives the box stays
 * empty at its final size, so no shape flashes before the pet; a pet that
 * does not exist here (deleted, or drawn on another machine) shows the shape.
 */
export function CompanionMark(props: MarkProps) {
  const { appearance, size, thinking = false, expressive = false } = props;
  const shape = <AgentSymbol {...appearance} size={size} thinking={thinking} expressive={expressive} />;
  // Outside a query client (an isolated render, a bare preview) there is no catalog to read.
  const client = useContext(QueryClientContext);
  if (!appearance.pet || !client) return shape;
  return <PetFace {...props} petId={appearance.pet} fallback={shape} />;
}

function PetFace({ petId, size, thinking = false, state, fallback }: MarkProps & { petId: string; fallback: JSX.Element }) {
  const pet = usePetById(petId);
  const staged = useContext(PetStageContext);
  if (pet === undefined) return <span aria-hidden className="inline-block shrink-0" style={{ width: size, height: size }} />;
  if (pet === null) return fallback;
  return <span className="agent-pet-face inline-flex shrink-0" data-agent-pet={pet.id}>
    <PetSprite pet={pet} state={state ?? staged ?? (thinking ? "working" : "idle")} px={size} />
  </span>;
}
