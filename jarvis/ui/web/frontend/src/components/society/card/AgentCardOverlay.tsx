/**
 * The agent card is a chat workspace: agent selection, conversation and actions.
 * The workspace can be embedded in the section or opened as a modal.
 * Radix Dialog provides focus containment and Escape-to-close.
 */
import type { ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { useLocaleChunk, useT } from "@/i18n";

import { AgentSwatch } from "../AgentSwatch";
import type { SocietyAgent } from "../data";
import { AgentChatPanel } from "../chat/AgentChatPanel";
import { RosterRail } from "../roster/RosterRail";
import type { SocietyChatGroup } from "@/lib/societyChatGroups";
import { OptionsRail } from "./OptionsRail";

export interface AgentCardOverlayProps {
  agent: SocietyAgent | null;
  /** Every agent — the rail lists them, the chat @mentions them. */
  roster: SocietyAgent[];
  groups?: SocietyChatGroup[];
  onSelectGroup?: (groupId: string) => void;
  /** True while the roster is still loading; the rail says so. */
  rosterLoading?: boolean;
  /** True while rows come from the sample roster rather than society.db. */
  sample?: boolean;
  /** A row in the rail was clicked: the card swaps to that agent. */
  onSelectAgent?: (agentId: string) => void;
  /** "+" in the rail opens the existing creator. */
  onCreate?: () => void;
  onClose: () => void;
  /** Render as the section workspace instead of a modal. */
  embedded?: boolean;
  /** Drawn at the top of the agent list, in the gray column. */
  railHeader?: ReactNode;
}

export function AgentCardOverlay({
  agent,
  roster,
  groups = [],
  onSelectGroup,
  rosterLoading = false,
  sample = false,
  onSelectAgent,
  onCreate,
  onClose,
  embedded = false,
  railHeader,
}: AgentCardOverlayProps) {
  const t = useT();
  useLocaleChunk("society");
  // The modal keeps a real dialog title plus its close control. The embedded
  // workspace hides this row entirely: the left rail already marks the active
  // agent and the right rail names it, so the row was a second, boring band.
  const Title = embedded ? "h2" : Dialog.Title;
  const Description = embedded ? "p" : Dialog.Description;
  const content = (
    <>
      {agent ? (
        <>
          {!embedded && (
          <header className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-1.5">
            <div data-testid="agent-card-identity" className="flex min-w-0 flex-1 items-center gap-2">
              <AgentSwatch agent={agent} size={32} />
              <div className="min-w-0 flex-1">
                <Title className="truncate font-display text-base font-semibold tracking-tight text-foreground">
                  {agent.name}
                </Title>
                <Description className="truncate text-xs text-muted-foreground">
                  {agent.title}
                </Description>
              </div>
            </div>
            <Badge variant="outline">{t(`society.state.${agent.state}`)}</Badge>
            <button type="button" onClick={onClose} aria-label={t("society.card.close")}
              className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground">
              <X className="h-4 w-4" aria-hidden />
            </button>
          </header>
          )}
          <div className="grid min-h-0 flex-1 grid-cols-[minmax(240px,300px)_minmax(0,1fr)]">
            <RosterRail
              agents={roster}
              groups={groups}
              onOpenGroup={onSelectGroup}
              loading={rosterLoading}
              sample={sample}
              activeAgentId={agent.agentId}
              onOpen={(id) => { if (id !== agent.agentId) onSelectAgent?.(id); }}
              onCreate={() => onCreate?.()}
              side="left"
              className="w-full border-0 jarvis-nav-surface"
              header={railHeader}
            />
            {/* Inner reading pane: same ground, divider and corner as the
                window sheet, but WITHOUT its top border. Top + left borders
                meet exactly at the rounded corner, and on a fractional grid
                seam that joint rasterizes as a small step. The gray caption
                above already separates by ground, so one border is enough. */}
            <div className="grid min-h-0 grid-cols-[minmax(0,1fr)_minmax(280px,320px)] overflow-hidden rounded-tl-[12px] border-l border-border bg-background">
            <section
              className="flex min-h-0 flex-col"
              aria-label={t("society.card.chat")}
              data-testid="agent-card-chat"
            >
              <AgentChatPanel key={agent.agentId} agent={agent} roster={roster} />
            </section>
            <OptionsRail agent={agent} onRetired={onClose} sample={sample} />
            </div>
          </div>
        </>
      ) : null}
    </>
  );
  if (embedded) {
    return <div data-testid="agent-card" className="flex min-h-0 flex-1 flex-col overflow-hidden bg-card">{content}</div>;
  }
  return (
    <Dialog.Root open={agent !== null} onOpenChange={(next) => (next ? undefined : onClose())}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-scrim/60 backdrop-blur-sm" />
        <Dialog.Content data-testid="agent-card" className="fixed inset-0 z-50 flex flex-col overflow-hidden bg-card focus:outline-none">
          {content}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
