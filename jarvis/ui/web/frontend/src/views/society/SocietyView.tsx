/**
 * The Agents section as the society (MASTERPLAN §4.1): left, the app's own
 * sidebar (unchanged, outside this view); centre, the stage; right, the fixed
 * agents rail. A thin strip above the stage carries the counts. Clicking a
 * row in the rail — or a figure on the island — opens the model card above
 * the stage; "+" opens the creator.
 *
 * The stage is the existing Jarvis-Agents board until the island (M3, built in
 * a parallel session under components/society/world/) mounts here — the
 * board then moves to the Ledger tab. The section keeps its id "agents", so
 * deep links, the navigate tool and the section parity tests are untouched.
 */
import { lazy, Suspense, useCallback, useMemo, useState } from "react";

import { useT } from "@/i18n";
import { AgentCardOverlay } from "@/components/society/card/AgentCardOverlay";
import { BuildingCardOverlay } from "@/components/society/card/BuildingCardOverlay";
import { isBuildingPlace, type BuildingPlace } from "@/components/society/card/buildingCards";
import { CreateAgentDialog } from "@/components/society/create/CreateAgentDialog";
import type { PlaceId } from "@/components/society/world/islandLayout";
import { useSocietyRoster } from "@/components/society/data";
import { RosterRail } from "@/components/society/roster/RosterRail";
import { useModelMenuData } from "@/components/society/chat/useModelMenuData";
import { MachinesButton } from "@/components/society/MachinesPanel";

const JarvisAgentsBoard = lazy(() =>
  import("@/views/JarvisAgentsView").then((m) => ({ default: m.JarvisAgentsView })),
);

export function SocietyView() {
  useModelMenuData();
  const t = useT();
  const roster = useSocietyRoster();
  const agents = useMemo(() => roster.data?.agents ?? [], [roster.data]);
  const sample = roster.data?.sample ?? true;
  const [openAgentId, setOpenAgentId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const openAgent = useMemo(
    () => agents.find((a) => a.agentId === openAgentId) ?? null,
    [agents, openAgentId],
  );
  const activeCount = agents.filter((a) => a.state === "working" || a.state === "waiting").length;

  const onCreated = useCallback(() => {
    setCreating(false);
    // The card does NOT open on top of the island: a brand-new agent is
    // walking out of the foundry right now, and its card would cover exactly
    // that. It holds nothing new anyway — the maker just typed all of it. The
    // rail lists the newcomer; a click on it, or on the figure, opens it.
  }, []);

  // A figure clicked on the island opens its card, like a row in the rail
  // (maintainer, 2026-09-02). Clearing the island's selection (a click on
  // the ground) leaves the card alone — it has its own close.
  const onIslandSelect = useCallback((agentId: string | null) => {
    if (agentId) setOpenAgentId(agentId);
  }, []);

  // A building clicked on the island opens its own card: the building
  // rendered as it stands on the map, and beside it what it does.
  const [openPlace, setOpenPlace] = useState<BuildingPlace | null>(null);
  const onIslandPlace = useCallback((place: PlaceId) => {
    if (isBuildingPlace(place)) setOpenPlace(place);
  }, []);

  return (
    <div className="flex h-full min-h-0 w-full" data-testid="society-view">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex h-9 shrink-0 items-center gap-3 border-b border-border px-4 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{t("society.strip.title")}</span>
          <span aria-live="polite">
            {t("society.strip.active").replace("{0}", String(activeCount))}
          </span>
          <span className="ml-auto"><MachinesButton /></span>
        </div>
        <div className="relative min-h-0 flex-1">
          <Suspense fallback={null}>
            <JarvisAgentsBoard onSelectAgent={onIslandSelect} onSelectPlace={onIslandPlace} />
          </Suspense>
        </div>
      </div>
      <RosterRail
        agents={agents}
        loading={roster.isLoading}
        sample={sample}
        activeAgentId={openAgentId}
        onOpen={setOpenAgentId}
        onCreate={() => setCreating(true)}
      />
      <AgentCardOverlay
        agent={openAgent}
        roster={agents}
        rosterLoading={roster.isLoading}
        sample={sample}
        onSelectAgent={setOpenAgentId}
        onCreate={() => {
          // The creator ends with the newcomer walking out of the foundry on
          // the island; the card sits on top of exactly that, so it closes.
          setOpenAgentId(null);
          setCreating(true);
        }}
        onClose={() => setOpenAgentId(null)}
      />
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
