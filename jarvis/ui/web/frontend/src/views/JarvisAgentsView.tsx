/** Lazy map surface for the Agents section. */
import { Suspense, lazy } from "react";
import type { PlaceId } from "@/components/society/world/islandLayout";

const WorldStage = lazy(() =>
  import("@/components/society/world/WorldStage").then((m) => ({ default: m.WorldStage })),
);

export interface JarvisAgentsViewProps {
  onSelectAgent?: (agentId: string | null) => void;
  onSelectPlace?: (place: PlaceId) => void;
  onOpenAgents: () => void;
}

export function JarvisAgentsView({ onSelectAgent, onSelectPlace, onOpenAgents }: JarvisAgentsViewProps) {
  return (
    <div className="h-full min-h-0">
      <Suspense fallback={<div className="h-full w-full animate-pulse bg-secondary" aria-hidden />}>
        <WorldStage onOpenAgents={onOpenAgents} onSelectAgent={onSelectAgent} onSelectPlace={onSelectPlace} />
      </Suspense>
    </div>
  );
}
