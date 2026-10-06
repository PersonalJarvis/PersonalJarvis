import { lazy, Suspense, useCallback, useMemo, useState, useEffect } from "react";
import { createPortal } from "react-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Building2, Users } from "lucide-react";

import { setMapFullscreen } from "@/lib/mapFullscreen";
import { inDesktopShell } from "@/lib/nativeDrop";
import { useLocaleChunk, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import {
  CAPTION_ICON_CLASS, CAPTION_ICON_STROKE, CAPTION_SEGMENT, CAPTION_SEGMENT_OFF, CAPTION_SEGMENT_ON,
  CAPTION_SEGMENT_PX, CAPTION_THUMB, CAPTION_TRACK,
} from "@/components/layout/captionSwitch";
import { AgentCardOverlay } from "@/components/society/card/AgentCardOverlay";
import { BrowserProfilesButton } from "@/components/society/browser/BrowserProfilesButton";
import { BUILDING_CARDS, isBuildingPlace, type BuildingPlace } from "@/components/society/card/buildingCards";
import { DeferredSocietyDialog } from "@/components/society/card/DeferredSocietyDialog";
import type { PlaceId } from "@/components/society/world/islandLayout";
import { useSocietyRoster } from "@/components/society/data";
import { RosterRail } from "@/components/society/roster/RosterRail";
import { useModelMenuData } from "@/components/society/chat/useModelMenuData";
import { ChatGroupPanel } from "@/components/society/chat/ChatGroupPanel";
import { useSocietyChatStore } from "@/components/society/chat/AgentChatPanel";
import { createSocietyChatGroup, updateSocietyChatGroup, useSocietyChatGroups } from "@/lib/societyChatGroups";
import { CanvasActivity } from "@/hooks/useCanvasAwake";
import { forgetLastAgentId, rememberLastAgentId, storedLastAgentId } from "./lastAgent";

/** The page's two faces, in caption order: the Verse map, then the agent roster. */
const MODES = [
  { value: "world", labelKey: "society.world.mode_map", Icon: Building2 },
  { value: "agents", labelKey: "society.roster.title", Icon: Users },
] as const;

const JarvisAgentsBoard = lazy(() =>
  import("@/views/JarvisAgentsView").then((m) => ({ default: m.JarvisAgentsView })),
);

// Do not evaluate either dialog's 3D dependencies until someone opens it.
const loadBuildingDialog = () => import("@/components/society/card/BuildingCardOverlay")
  .then((module) => ({ default: module.BuildingCardOverlay }));
const loadCreateDialog = () => import("@/components/society/create/CreateAgentDialog")
  .then((module) => ({ default: module.CreateAgentDialog }));


function isProtectedMarsInteraction(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean(target.closest("[data-mars-ui], [data-mars-mode=\'player\'], [data-mars-mode=\'follow\']"));
}

export function SocietyView() {
  const queryClient = useQueryClient();
  useModelMenuData();
  const t = useT();
  useLocaleChunk("society");
  const [mode, setMode] = useState<"agents" | "world">("agents");
  const roster = useSocietyRoster();
  const agents = useMemo(() => roster.data?.agents ?? [], [roster.data]);
  const sample = roster.data?.sample ?? true;
  const groupsQuery = useSocietyChatGroups(!sample);
  const groups = useMemo(() => groupsQuery.data ?? [], [groupsQuery.data]);
  const [openGroupId, setOpenGroupId] = useState<string | null>(null);
  const openGroup = groups.find((group) => group.group_id === openGroupId) ?? null;
  const [openAgentId, setOpenAgentId] = useState<string | null>(storedLastAgentId);
  const [creating, setCreating] = useState(false);
  const [groupError, setGroupError] = useState("");
  const [openPlace, setOpenPlace] = useState<BuildingPlace | null>(null);

  const openAgent = useMemo(
    () => agents.find((a) => a.agentId === openAgentId) ?? agents.find((a) => a.tier === "lead") ?? agents[0] ?? null,
    [agents, openAgentId],
  );

  const selectAgent = useCallback((agentId: string | null) => {
    setOpenGroupId(null);
    setOpenAgentId(agentId);
    if (agentId) rememberLastAgentId(agentId);
  }, []);

  const selectGroup = useCallback((groupId: string) => {
    useSocietyChatStore.getState().disconnect();
    setOpenGroupId(groupId);
    setOpenAgentId(null);
  }, []);

  const groupAgents = useCallback((sourceId: string, targetId: string) => {
    const source = agents.find((agent) => agent.agentId === sourceId);
    const target = agents.find((agent) => agent.agentId === targetId);
    if (sample || !source || !target || sourceId === targetId) return;
    setGroupError("");
    void createSocietyChatGroup(`${source.name} + ${target.name}`, [sourceId, targetId])
      .then(async (group) => {
        await queryClient.invalidateQueries({ queryKey: ["society", "chat-groups"] });
        selectGroup(group.group_id);
      })
      .catch((error) => setGroupError(error instanceof Error ? error.message : String(error)));
  }, [agents, queryClient, sample, selectGroup]);

  const addAgentToGroup = useCallback((agentId: string, groupId: string) => {
    const group = groups.find((entry) => entry.group_id === groupId);
    if (sample || !group || group.members.includes(agentId)) return;
    setGroupError("");
    void updateSocietyChatGroup(groupId, group.name, [...group.members, agentId])
      .then(async () => {
        await queryClient.invalidateQueries({ queryKey: ["society", "chat-groups"] });
        selectGroup(groupId);
      })
      .catch((error) => setGroupError(error instanceof Error ? error.message : String(error)));
  }, [groups, queryClient, sample, selectGroup]);

  useEffect(() => {
    if (openGroupId && groupsQuery.data && !groups.some((group) => group.group_id === openGroupId)) setOpenGroupId(null);
  }, [groups, groupsQuery.data, openGroupId]);

  useEffect(() => {
    if (openAgentId && agents.length > 0 && !agents.some((agent) => agent.agentId === openAgentId)) {
      setOpenAgentId(null);
      forgetLastAgentId();
    }
  }, [agents, openAgentId]);

  // The new agent is already on the rail (the create patched the roster), so
  // it opens straight away — the person's next step is almost always with it.
  const onCreated = useCallback((agentId: string) => {
    setCreating(false);
    selectAgent(agentId);
  }, [selectAgent]);

  const [fullscreenError, setFullscreenError] = useState(false);
  const switchMode = useCallback((next: "agents" | "world") => {
    setMode(next);
    setFullscreenError(false);
    if (inDesktopShell()) void setMapFullscreen(next === "world").catch(() => setFullscreenError(true));
  }, []);

  useEffect(() => {
    // A reload starts in Agents; restore a native window left fullscreen by it.
    if (inDesktopShell()) void setMapFullscreen(false).catch(() => setFullscreenError(true));
    return () => {
      void setMapFullscreen(false).catch((error) => console.warn("Fullscreen exit failed", error));
    };
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented && !isProtectedMarsInteraction(event.target) && mode === "world" && !openPlace && !creating) switchMode("agents");
    };
    const onFullscreen = () => {
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
      selectAgent(agentId);
      switchMode("agents");
    }
  }, [selectAgent, switchMode]);

  // A building clicked on the island opens its own card: the building
  // rendered as it stands on the map, and beside it what it does.
  const onIslandPlace = useCallback((place: PlaceId) => {
    if (isBuildingPlace(place)) setOpenPlace(place);
  }, []);

  // Icon-only, in the same caption look as the Agentic IDE's switch; the
  // names live on as the accessible label and the hover title.
  const modeIndex = MODES.findIndex((item) => item.value === mode);
  const modeSwitch = (
    <div role="tablist" aria-label={t("society.world.mode_label")} className={CAPTION_TRACK}>
      <span aria-hidden className={CAPTION_THUMB}
        style={{ width: CAPTION_SEGMENT_PX, transform: `translateX(${Math.max(0, modeIndex) * CAPTION_SEGMENT_PX}px)` }} />
      {MODES.map(({ value, labelKey, Icon }) => {
        const label = t(labelKey);
        return <button key={value} type="button" role="tab" aria-selected={mode === value}
          aria-label={label} title={label} data-testid={`society-mode-${value}`}
          onClick={() => switchMode(value)}
          style={{ width: CAPTION_SEGMENT_PX }}
          className={cn(CAPTION_SEGMENT, mode === value ? CAPTION_SEGMENT_ON : CAPTION_SEGMENT_OFF)}>
          <Icon aria-hidden className={CAPTION_ICON_CLASS} strokeWidth={CAPTION_ICON_STROKE} />
        </button>;
      })}
    </div>
  );

  return (
    <div className={mode === "world" ? "fixed inset-x-0 bottom-0 top-8 z-30 flex flex-col bg-background" : "relative flex h-full min-h-0 w-full flex-col"} data-testid="society-view" data-tour="agents-page">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {/* Map mode takes the native window fullscreen, so the switch cannot
            live inside the map HUD: it would shrink into the corner and strand
            the user on the island. It rides in the window caption in BOTH
            modes — one switch, always centered, always a way back. */}
        {createPortal(
          <div className="pointer-events-none fixed inset-x-0 top-0 z-[140] flex h-8 items-center justify-center" data-testid="mode-switch">
            <div className="pointer-events-auto flex items-center gap-2">{modeSwitch}<BrowserProfilesButton iconOnly /></div>
          </div>,
          document.body,
        )}
        {fullscreenError && <p role="alert" className="bg-card px-4 py-2 text-sm text-destructive">{t("society.world.fullscreen_failed")}</p>}
        {groupError && <p role="alert" className="bg-card px-4 py-2 text-sm text-destructive">{groupError}</p>}
        {mode === "world" ? (
        <div className="relative flex min-h-0 flex-1">
          <div className="min-w-0 flex-1">
            <CanvasActivity.Provider value={!openPlace && !creating}>
              <Suspense fallback={null}>
                <JarvisAgentsBoard onSelectAgent={onIslandSelect} onSelectPlace={onIslandPlace} onOpenAgents={() => switchMode("agents")}
                  onCreateAgent={() => setCreating(true)} onOpenGroup={(groupId) => { selectGroup(groupId); switchMode("agents"); }} />
              </Suspense>
            </CanvasActivity.Provider>
          </div>

        </div>
        ) : null}
        <div className={mode === "agents" ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
        {openGroup ? (
          <ChatGroupPanel group={openGroup} groups={groups} roster={agents} onOpenAgent={selectAgent} onOpenGroup={selectGroup}
            onCreateAgent={() => setCreating(true)} onDeleted={() => setOpenGroupId(null)}
            onGroupAgents={groupAgents} onAddAgentToGroup={addAgentToGroup} />
        ) : openAgent ? (
          <AgentCardOverlay embedded agent={openAgent} roster={agents} rosterLoading={roster.isLoading}
            groups={groups} onSelectGroup={selectGroup}
            onGroupAgents={sample ? undefined : groupAgents} onAddAgentToGroup={sample ? undefined : addAgentToGroup}
            sample={sample} onSelectAgent={selectAgent} onCreate={() => setCreating(true)}
            onClose={() => setOpenAgentId(null)} />
        ) : (
          <RosterRail agents={agents} loading={roster.isLoading} sample={sample}
            groups={groups} onOpenGroup={selectGroup}
            onGroupAgents={sample ? undefined : groupAgents} onAddAgentToGroup={sample ? undefined : addAgentToGroup}
            activeAgentId={null} onOpen={selectAgent} onCreate={() => setCreating(true)} side="left"
            className="w-full border-0 jarvis-nav-surface" />
        )}
        </div>
      </div>
      {openPlace && <DeferredSocietyDialog
        load={loadBuildingDialog}
        title={t(`society.world.${BUILDING_CARDS[openPlace].nameKey}`)}
        onClose={() => setOpenPlace(null)}
        dialogProps={{
          place: openPlace,
          onClose: () => setOpenPlace(null),
          onCreateAgent: () => {
            setOpenPlace(null);
            setCreating(true);
          },
        }}
      />}
      {creating && <DeferredSocietyDialog
        load={loadCreateDialog}
        title={t("society.create.title")}
        onClose={() => setCreating(false)}
        dialogProps={{ open: true, onClose: () => setCreating(false), onCreated }}
      />}
    </div>
  );
}

export default SocietyView;
