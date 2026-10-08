/** Lazy map surface; the parent keeps the existing Agents workspace mounted. */
import { useLocaleChunk } from "@/i18n";
import type { PlaceId } from "@/components/society/world/islandLayout";
import { OfficeStage } from "@/components/society/office/OfficeStage";

export interface JarvisAgentsViewProps {
  onSelectAgent?: (agentId: string | null) => void;
  onSelectPlace?: (place: PlaceId) => void;
  onOpenAgents: () => void;
  onCreateAgent?: () => void;
  onOpenGroup?: (groupId: string) => void;
  active?: boolean;
}

export function JarvisAgentsView({ onSelectAgent, onOpenAgents, onCreateAgent, onOpenGroup, active = true }: JarvisAgentsViewProps) {
  const ready = useLocaleChunk("society");
  if (!ready) return null;
  return (
    <div className="h-full min-h-0">
      <OfficeStage active={active} onOpenLedger={onOpenAgents} onSelectAgent={onSelectAgent} onCreateAgent={onCreateAgent} onOpenGroup={onOpenGroup} />
    </div>
  );
}
