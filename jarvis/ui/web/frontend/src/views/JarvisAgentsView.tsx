/** Lazy map surface; the parent keeps the existing Agents workspace mounted. */
import { Suspense, lazy, useCallback, useState } from "react";
import { useLocaleChunk } from "@/i18n";
import type { PlaceId } from "@/components/society/world/islandLayout";

const MarsWorldStage = lazy(() => import("@/components/society/mars/MarsWorldStage").then((m) => ({ default: m.MarsWorldStage })));
const MarsStationPanel = lazy(() => import("@/components/society/mars/MarsStationPanel").then((m) => ({ default: m.MarsStationPanel })));

export interface JarvisAgentsViewProps {
  onSelectAgent?: (agentId: string | null) => void;
  onSelectPlace?: (place: PlaceId) => void;
  onOpenAgents: () => void;
}

export function JarvisAgentsView({ onSelectAgent, onOpenAgents }: JarvisAgentsViewProps) {
  const ready = useLocaleChunk("society");
  const [stationOpen, setStationOpen] = useState(false);
  const openStation = useCallback(() => setStationOpen(true), []);
  if (!ready) return null;
  return (
    <div className="h-full min-h-0">
      <Suspense fallback={<div className="h-full w-full animate-pulse bg-secondary" aria-hidden />}>
        <MarsWorldStage onOpenLedger={onOpenAgents}
          onSelectAgent={onSelectAgent} onOpenStation={openStation}
          stationPanel={stationOpen ? <MarsStationPanel onClose={() => setStationOpen(false)} onOpenAgent={onSelectAgent} /> : undefined}
        />
      </Suspense>
    </div>
  );
}
