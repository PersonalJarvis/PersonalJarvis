import { createContext } from "react";
import type { PetState } from "@/lib/petStates";

/**
 * The pet row a stage asks every pet face inside it to play. The agent
 * chat's bot stage sets it per scene (searching sniffs, typing talks), so an
 * agent that wears a pet acts out the same moment its shape would. Null
 * outside a stage: the face picks its own row.
 */
export const PetStageContext = createContext<PetState | null>(null);
