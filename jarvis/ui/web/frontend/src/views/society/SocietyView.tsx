import { lazy, Suspense, useCallback, useMemo, useState, useEffect } from "react";

import { useSocietyShell } from "@/store/societyShell";
import { setMapFullscreen } from "@/lib/mapFullscreen";
import { inDesktopShell } from "@/lib/nativeDrop";
import { useLocaleChunk, useT } from "@/i18n";
import { AgentCardOverlay } from "@/components/society/card/AgentCardOverlay";
import { BuildingCardOverlay } from "@/components/society/card/BuildingCardOverlay";
import { isBuildingPlace, type BuildingPlace } from "@/components/society/card/buildingCards";
import { CreateAgentDialog } from "@/components/society/create/CreateAgentDialog";
import type { PlaceId } from "@/components/society/world/islandLayout";
import { useSocietyRoster } from "@/components/society/data";
import { RosterRail } from "@/components/society/roster/RosterRail";
import { useModelMenuData } from "@/components/society/chat/useModelMenuData";
import { CanvasActivity } from "@/hooks/useCanvasAwake";
import { forgetLastAgentId, rememberLastAgentId, storedLastAgentId } from "./lastAgent";

const JarvisAgentsBoard = lazy(() =>
  import("@/views/JarvisAgentsView").then((m) => ({ default: m.JarvisAgentsView })),
);

export function SocietyView() {
  useModelMenuData();
  const t = useT();
  useLocaleChunk("society");
  const [mode, setMode] = useState<"agents" | "world">("agents");
  const roster = useSocietyRoster();
  const agents = useMemo(() => roster.data?.agents ?? [], [roster.data]);
  const sample = roster.data?.sample ?? true;
  const [openAgentId, setOpenAgentId] = useState<string | null>(storedLastAgentId);
  const [creating, setCreating] = useState(false);
  const [openPlace, setOpenPlace] = useState<BuildingPlace | null>(null);

  const openAgent = useMemo(
    () => agents.find((a) => a.agentId === openAgentId) ?? agents.find((a) => a.tier === "lead") ?? agents[0] ?? null,
    [agents, openAgentId],
  );

  const selectAgent = useCallback((agentId: string | null) => {
    setOpenAgentId(agentId);
    if (agentId) rememberLastAgentId(agentId);
  }, []);

  useEffect(() => {
    if (openAgentId && agents.length > 0 && !agents.some((agent) => agent.agentId === openAgentId)) {
      setOpenAgentId(null);
      forgetLastAgentId();
    }
  }, [agents, openAgentId]);

  const onCreated = useCallback(() => {
    setCreating(false);
  }, []);

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
      if (event.key === "Escape" && mode === "world" && !openPlace && !creating) switchMode("agents");
    };
    const onFullscreen = () => {
      if (!document.fullscreenElement && !inDesktopShell()) setMode("agents");
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
      selectAgent(agentId);
      switchMode("agents");
    }
  }, [selectAgent, switchMode]);

  // A building clicked on the island opens its own card: the building
  // rendered as it stands on the map, and beside it what it does.
  const onIslandPlace = useCallback((place: PlaceId) => {
    if (isBuildingPlace(place)) setOpenPlace(place);
  }, []);

  const modeSwitch = (
    <div role="tablist" aria-label={t("society.world.mode_label")} className="flex items-center gap-0.5 rounded-md border border-border/60 bg-background/80 p-0.5 backdrop-blur-sm">
      {(["world", "agents"] as const).map((value) => {
        return <button key={value} type="button" role="tab" aria-selected={mode === value}
          onClick={() => switchMode(value)}
          className={`inline-flex h-5 items-center justify-center rounded px-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${mode === value ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground"}`}>
          {t(value === "world" ? "society.world.mode_map" : "society.roster.title")}
        </button>;
      })}
    </div>
  );

  return (
    <div className={mode === "world" ? "fixed inset-x-0 bottom-0 top-8 z-30 flex flex-col bg-background" : "flex h-full min-h-0 w-full flex-col"} data-testid="society-view">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {/* Map mode takes the native window fullscreen, so the switch cannot
            live inside the map HUD: it would shrink into the corner and strand
            the user on the island. It rides in the window caption in BOTH
            modes — one switch, always centered, always a way back. */}
        <div className="pointer-events-none fixed inset-x-0 top-0 z-[130] flex h-8 items-center justify-center" data-testid="mode-switch">
          <div className="pointer-events-auto">{modeSwitch}</div>
        </div>
        {fullscreenError && <p role="alert" className="bg-card px-4 py-2 text-sm text-destructive">{t("society.world.fullscreen_failed")}</p>}
        {mode === "world" ? (
        <div className="relative flex min-h-0 flex-1">
          <div className="min-w-0 flex-1">
            <CanvasActivity.Provider value={!openPlace && !creating}>
              <Suspense fallback={null}>
                <JarvisAgentsBoard onSelectAgent={onIslandSelect} onSelectPlace={onIslandPlace} onOpenAgents={() => switchMode("agents")} />
              </Suspense>
            </CanvasActivity.Provider>
          </div>

        </div>
        ) : null}
        <div className={mode === "agents" ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
        {openAgent ? (
          <AgentCardOverlay embedded agent={openAgent} roster={agents} rosterLoading={roster.isLoading}
            sample={sample} onSelectAgent={selectAgent} onCreate={() => setCreating(true)}
            onClose={() => setOpenAgentId(null)} />
        ) : (
          <RosterRail agents={agents} loading={roster.isLoading} sample={sample}
            activeAgentId={null} onOpen={selectAgent} onCreate={() => setCreating(true)} side="left"
            className="w-full border-0 jarvis-nav-surface" />
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
    </div>
  );
}

export default SocietyView;
