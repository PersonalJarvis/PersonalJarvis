/** Lazy map surface; the parent keeps the existing Agents workspace mounted. */
import { Suspense, lazy, useCallback, useState } from "react";
import { useLocaleChunk, useT } from "@/i18n";
import { useAppInstance } from "@/hooks/useAppInstance";
import type { PlaceId } from "@/components/society/world/islandLayout";

const WorldStage = lazy(() => import("@/components/society/world/WorldStage").then((m) => ({ default: m.WorldStage })));
const MarsWorldStage = lazy(() => import("@/components/society/mars/MarsWorldStage").then((m) => ({ default: m.MarsWorldStage })));
const MarsStationPanel = lazy(() => import("@/components/society/mars/MarsStationPanel").then((m) => ({ default: m.MarsStationPanel })));

export interface JarvisAgentsViewProps {
  onSelectAgent?: (agentId: string | null) => void;
  onSelectPlace?: (place: PlaceId) => void;
  onOpenAgents: () => void;
  onMarsSelectionChange?: (selected: boolean) => void;
}

export function JarvisAgentsView({ onSelectAgent, onSelectPlace, onOpenAgents, onMarsSelectionChange }: JarvisAgentsViewProps) {
  const t = useT();
  const ready = useLocaleChunk("society");
  const instance = useAppInstance();
  const [selectedMars, setMars] = useState(() => new URLSearchParams(window.location.search).get("world") === "mars");
  const mars = instance?.isDev || selectedMars;
  const [stationOpen, setStationOpen] = useState(false);
  const openStation = useCallback(() => setStationOpen(true), []);
  const switchWorld = () => {
    const next = !mars;
    const url = new URL(window.location.href);
    if (next) url.searchParams.set("world", "mars");
    else url.searchParams.delete("world");
    window.history.replaceState(window.history.state, "", url);
    setMars(next); setStationOpen(false);
    onMarsSelectionChange?.(next);
  };
  if (!ready || !instance) return <div className="h-full w-full animate-pulse bg-secondary" aria-busy="true" />;
  const switcher = instance.isDev ? undefined : <button type="button" onClick={switchWorld} data-mars-ui
    className="rounded border border-border bg-background px-2 py-1 text-xs text-foreground hover:bg-secondary">
    {t(mars ? "society.mars.previous_world" : "society.mars.open_preview")}
  </button>;
  return (
    <div className="h-full min-h-0">
      <Suspense fallback={<div className="h-full w-full animate-pulse bg-secondary" aria-hidden />}>
        {mars ? <MarsWorldStage topRight={switcher} onOpenLedger={onOpenAgents}
          onSelectAgent={onSelectAgent} onOpenStation={openStation}
          stationPanel={stationOpen ? <MarsStationPanel onClose={() => setStationOpen(false)} onOpenAgent={onSelectAgent} /> : undefined}
        /> : <WorldStage topRight={switcher} onOpenAgents={onOpenAgents} onSelectAgent={onSelectAgent} onSelectPlace={onSelectPlace} />}
      </Suspense>
    </div>
  );
}
