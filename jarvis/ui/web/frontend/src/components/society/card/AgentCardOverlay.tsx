/**
 * The agent card is a chat workspace: agent selection, conversation and actions.
 * The identity header is informational; there is no separate profile view.
 * Radix Dialog provides focus containment and Escape-to-close.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { useLocaleChunk, useT } from "@/i18n";

import { AgentSwatch } from "../AgentSwatch";
import type { SocietyAgent } from "../data";
import { AgentChatPanel } from "../chat/AgentChatPanel";
import { RosterRail } from "../roster/RosterRail";
import { OptionsRail } from "./OptionsRail";

export interface AgentCardOverlayProps {
  agent: SocietyAgent | null;
  /** Every agent — the rail lists them, the chat @mentions them. */
  roster: SocietyAgent[];
  /** True while the roster is still loading; the rail says so. */
  rosterLoading?: boolean;
  /** True while rows come from the sample roster rather than society.db. */
  sample?: boolean;
  /** A row in the rail was clicked: the card swaps to that agent. */
  onSelectAgent?: (agentId: string) => void;
  /** "+" in the rail. The creator wants the island, so the card closes first. */
  onCreate?: () => void;
  onClose: () => void;
}

export function AgentCardOverlay({
  agent,
  roster,
  rosterLoading = false,
  sample = false,
  onSelectAgent,
  onCreate,
  onClose,
}: AgentCardOverlayProps) {
  const t = useT();
  useLocaleChunk("society");
  return (
    <Dialog.Root open={agent !== null} onOpenChange={(next) => (next ? undefined : onClose())}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-scrim/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="agent-card"
          className="fixed inset-4 z-50 flex flex-col overflow-hidden rounded-lg border border-border bg-card shadow-float focus:outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none lg:inset-10"
        >
          {agent ? (
            <>
              <header className="flex shrink-0 items-center gap-3 border-b border-border px-5 py-3">
                <div data-testid="agent-card-identity" className="flex min-w-0 flex-1 items-center gap-3">
                  <AgentSwatch agent={agent} size={40} />
                  <div className="min-w-0 flex-1">
                    <Dialog.Title className="truncate font-display text-base font-semibold tracking-tight text-foreground">
                      {agent.name}
                    </Dialog.Title>
                    <Dialog.Description className="truncate text-xs text-muted-foreground">
                      {agent.title}
                    </Dialog.Description>
                  </div>
                </div>
                <Badge variant="outline">{t(`society.state.${agent.state}`)}</Badge>
                <Dialog.Close
                  aria-label={t("society.card.close")}
                  className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong"
                >
                  <X className="h-4 w-4" aria-hidden />
                </Dialog.Close>
              </header>
              <div className="grid min-h-0 flex-1 grid-cols-[minmax(220px,1fr)_minmax(0,5fr)_minmax(300px,320px)]">
                <RosterRail
                  agents={roster}
                  loading={rosterLoading}
                  sample={sample}
                  activeAgentId={agent.agentId}
                  onOpen={(id) => { if (id !== agent.agentId) onSelectAgent?.(id); }}
                  onCreate={() => onCreate?.()}
                  side="left"
                  className="w-full"
                />
                <section
                  className="flex min-h-0 flex-col"
                  aria-label={t("society.card.chat")}
                  data-testid="agent-card-chat"
                >
                  <AgentChatPanel agent={agent} roster={roster} />
                </section>
                <OptionsRail agent={agent} onRetired={onClose} sample={sample} />
              </div>
            </>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
