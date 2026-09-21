/** Lazy map surface for the Agents section. */
import { Suspense, lazy, type ReactNode } from "react";
import type { PlaceId } from "@/components/society/world/islandLayout";

const WorldStage = lazy(() =>
  import("@/components/society/world/WorldStage").then((m) => ({ default: m.WorldStage })),
);

export interface JarvisAgentsViewProps {
  onSelectAgent?: (agentId: string | null) => void;
  onSelectPlace?: (place: PlaceId) => void;
  onOpenAgents: () => void;
  /** App-chrome content floating over the canvas (the Map / Agents switch). */
  topRight?: ReactNode;
}

export function JarvisAgentsView({ onSelectAgent, onSelectPlace, onOpenAgents, topRight }: JarvisAgentsViewProps) {
  return (
    <div className="h-full min-h-0">
      <Suspense fallback={<div className="h-full w-full animate-pulse bg-secondary" aria-hidden />}>
        <WorldStage onOpenAgents={onOpenAgents} onSelectAgent={onSelectAgent} onSelectPlace={onSelectPlace} topRight={topRight} />
      </Suspense>
    </div>
  );
}
