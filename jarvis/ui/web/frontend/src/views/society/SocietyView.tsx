import { lazy, Suspense, useCallback, useMemo, useState, useEffect } from "react";

import { PanelLeft } from "lucide-react";
import { useSocietyShell } from "@/store/societyShell";
import { setMapFullscreen } from "@/lib/mapFullscreen";
import { inDesktopShell } from "@/lib/nativeDrop";
import { useLocaleChunk, useT } from "@/i18n";
import { CodingModeBadge } from "@/components/layout/CodingModeBadge";
import { TopBarActions } from "@/components/layout/TopBar";
import { AgentCardOverlay } from "@/components/society/card/AgentCardOverlay";
import { BuildingCardOverlay } from "@/components/society/card/BuildingCardOverlay";
import { isBuildingPlace, type BuildingPlace } from "@/components/society/card/buildingCards";
import { CreateAgentDialog } from "@/components/society/create/CreateAgentDialog";
import type { PlaceId } from "@/components/society/world/islandLayout";
import { useSocietyRoster } from "@/components/society/data";
import { RosterRail } from "@/components/society/roster/RosterRail";
import { useModelMenuData } from "@/components/society/chat/useModelMenuData";
import { CanvasActivity } from "@/hooks/useCanvasAwake";

const JarvisAgentsBoard = lazy(() =>
  import("@/views/JarvisAgentsView").then((m) => ({ default: m.JarvisAgentsView })),
);
const MarsStationPanel = lazy(() => import("@/components/society/mars/MarsStationPanel").then((m) => ({ default: m.MarsStationPanel })));

function isProtectedMarsInteraction(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean(target.closest("[data-mars-ui], [data-mars-mode='player'], [data-mars-mode='follow']"));
}

export function SocietyView() {
  useModelMenuData();
  const t = useT();
  useLocaleChunk("society");
  const [mode, setMode] = useState<"agents" | "world">("agents");
  const [marsStationOpen, setMarsStationOpen] = useState(false);
  const [marsSelected, setMarsSelected] = useState(() => new URLSearchParams(window.location.search).get("world") === "mars");
  const onMarsSelectionChange = useCallback((selected: boolean) => {
    setMarsSelected(selected);
    if (!selected) setMarsStationOpen(false);
  }, []);
  const roster = useSocietyRoster();
  const agents = useMemo(() => roster.data?.agents ?? [], [roster.data]);
  const sample = roster.data?.sample ?? true;
  const [openAgentId, setOpenAgentId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [openPlace, setOpenPlace] = useState<BuildingPlace | null>(null);

  const openAgent = useMemo(
    () => agents.find((a) => a.agentId === openAgentId) ?? agents.find((a) => a.tier === "lead") ?? agents[0] ?? null,
    [agents, openAgentId],
  );

  const onCreated = useCallback(() => {
    setCreating(false);
  }, []);

  const navigationOpen = useSocietyShell((s) => s.navigationOpen);
  const toggleNavigation = useSocietyShell((s) => s.toggleNavigation);
  const [fullscreenError, setFullscreenError] = useState(false);
  const switchMode = useCallback((next: "agents" | "world") => {
    setMode(next);
    setFullscreenError(false);
    void setMapFullscreen(next === "world").catch(() => setFullscreenError(true));
  }, []);

  useEffect(() => {
    const reset = useSocietyShell.getState().reset;
    reset();
    // A reload starts in Agents; restore a native window left fullscreen by it.
    if (inDesktopShell()) void setMapFullscreen(false).catch(() => setFullscreenError(true));
    return () => {
      reset();
      void setMapFullscreen(false).catch((error) => console.warn("Fullscreen exit failed", error));
    };
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented && !isProtectedMarsInteraction(event.target) && mode === "world" && !openPlace && !creating) switchMode("agents");
    };
    const onFullscreen = () => {
      // Browser Escape can exit fullscreen without delivering a page keydown.
      // Preserve the map's focused form/player until its own controls leave it.
      if (!document.fullscreenElement && !inDesktopShell() && !isProtectedMarsInteraction(document.activeElement)) setMode("agents");
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("fullscreenchange", onFullscreen);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("fullscreenchange", onFullscreen);
    };
  }, [mode, creating, switchMode, openPlace]);

  const onIslandSelect = useCallback((agentId: string | null) => {
    if (agentId) {
      setOpenAgentId(agentId);
      switchMode("agents");
    }
  }, [switchMode]);

  // A building clicked on the island opens its own card: the building
  // rendered as it stands on the map, and beside it what it does.
  const onIslandPlace = useCallback((place: PlaceId) => {
    if (isBuildingPlace(place)) setOpenPlace(place);
  }, []);

  const modeSwitch = (
    <div role="tablist" aria-label={t("society.world.mode_label")} className="flex items-center gap-0.5 rounded-md border border-border/60 p-0.5">
      {(["world", "agents"] as const).map((value) => {
        return <button key={value} type="button" role="tab" aria-selected={mode === value}
          onClick={() => switchMode(value)}
          className={`inline-flex h-6 items-center justify-center rounded px-3 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${mode === value ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground"}`}>
          {t(value === "world" ? "society.world.mode_map" : "society.roster.title")}
        </button>;
      })}
    </div>
  );

  return (
    <div className={mode === "world" ? "fixed inset-0 z-30 flex flex-col bg-background" : "relative flex h-full min-h-0 w-full flex-col"} data-testid="society-view">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="jarvis-shell-surface flex h-12 shrink-0 items-center gap-2 border-b border-border px-4">
          <div className="flex min-w-0 flex-1 items-center gap-2">
          {mode === "agents" && <button type="button" onClick={toggleNavigation} aria-expanded={navigationOpen}
            aria-label={t("society.world.toggle_sections")} title={t("society.world.toggle_sections")}
            className="rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
            <PanelLeft className="h-4 w-4" aria-hidden />
          </button>}
            <span data-testid="topbar-view-title" className="truncate text-base font-medium text-foreground">
              {t("nav.agents")}
            </span>
          </div>
          {modeSwitch}
          {mode === "agents" && marsSelected && <button type="button" onClick={() => setMarsStationOpen(true)} className="rounded border border-border px-2 py-1 text-xs text-foreground">{t("society.mars.station_title")}</button>}
          <div className="flex min-w-0 flex-1 items-center justify-end gap-2">
            <CodingModeBadge />
            <TopBarActions />
          </div>
        </header>
        {fullscreenError && <p role="alert" className="bg-card px-4 py-2 text-sm text-destructive">{t("society.world.fullscreen_failed")}</p>}
        {mode === "world" ? (
        <div className="relative flex min-h-0 flex-1">
          <div className="min-w-0 flex-1">
            <CanvasActivity.Provider value={!openPlace && !creating}>
              <Suspense fallback={null}>
                <JarvisAgentsBoard onSelectAgent={onIslandSelect} onSelectPlace={onIslandPlace} onOpenAgents={() => switchMode("agents")} onMarsSelectionChange={onMarsSelectionChange} />
              </Suspense>
            </CanvasActivity.Provider>
          </div>

        </div>
        ) : null}
        <div className={mode === "agents" ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
        {openAgent ? (
          <AgentCardOverlay embedded agent={openAgent} roster={agents} rosterLoading={roster.isLoading}
            sample={sample} onSelectAgent={setOpenAgentId} onCreate={() => setCreating(true)}
            onClose={() => setOpenAgentId(null)} />
        ) : (
          <RosterRail agents={agents} loading={roster.isLoading} sample={sample}
            activeAgentId={null} onOpen={setOpenAgentId} onCreate={() => setCreating(true)} side="left" />
        )}
        </div>
      </div>
      <BuildingCardOverlay
        place={openPlace}
        onClose={() => setOpenPlace(null)}
        onCreateAgent={() => {
          setOpenPlace(null);
          setCreating(true);
        }}
      />
      <CreateAgentDialog open={creating} onClose={() => setCreating(false)} onCreated={onCreated} />
      {mode === "agents" && marsSelected && marsStationOpen && <div className="absolute right-4 top-14 z-40 max-h-[calc(100%-4rem)] w-[min(26rem,calc(100%-2rem))] overflow-auto" data-mars-ui>
        <Suspense fallback={null}><MarsStationPanel onClose={() => setMarsStationOpen(false)} onOpenAgent={(id) => { setOpenAgentId(id); setMarsStationOpen(false); }} /></Suspense>
      </div>}
    </div>
  );
}

export default SocietyView;
