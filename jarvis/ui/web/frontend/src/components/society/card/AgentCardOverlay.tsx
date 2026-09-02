/**
 * The agent model card: a near-full-screen window above the world, the
 * world still visible through a dimmed, blurred scrim (MASTERPLAN §4.2).
 * Three columns, in this order: Specs | 3D figure | Chat. Esc and ✕ close.
 *
 * Radix Dialog gives the focus trap and Escape for free; the content is an
 * inset window rather than a centred box so the stage shows around it.
 * `components/layout/overlay-stacking.test.ts` guards the shell so a fixed
 * overlay like this one is never trapped under the nav column.
 *
 * The chat column is the ordinary agent chat once the roster row carries a
 * session (M2). A sample row carries none, and the column says so instead
 * of inventing a transcript.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { MessageSquareDashed, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { useT } from "@/i18n";

import { AgentSwatch } from "../AgentSwatch";
import type { SocietyAgent } from "../data";
import { AgentFigureViewer } from "../figures/AgentFigureViewer";
import { AgentSpecSheet } from "./AgentSpecSheet";

export interface AgentCardOverlayProps {
  agent: SocietyAgent | null;
  onClose: () => void;
}

export function AgentCardOverlay({ agent, onClose }: AgentCardOverlayProps) {
  const t = useT();
  const open = agent !== null;
  return (
    <Dialog.Root open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-scrim/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="agent-card"
          className="fixed inset-4 z-50 flex flex-col overflow-hidden rounded-lg border border-border bg-card shadow-float focus:outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none lg:inset-10"
        >
          {agent ? (
            <>
              <header className="flex shrink-0 items-center gap-3 border-b border-border px-5 py-3">
                <AgentSwatch agent={agent} size={40} />
                <div className="min-w-0 flex-1">
                  <Dialog.Title className="truncate font-display text-base font-semibold tracking-tight text-foreground">
                    {agent.name}
                  </Dialog.Title>
                  <Dialog.Description className="truncate text-xs text-muted-foreground">
                    {agent.title}
                  </Dialog.Description>
                </div>
                <Badge variant="outline">{t(`society.state.${agent.state}`)}</Badge>
                <Dialog.Close
                  aria-label={t("society.card.close")}
                  className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong"
                >
                  <X className="h-4 w-4" aria-hidden />
                </Dialog.Close>
              </header>
              <div className="grid min-h-0 flex-1 grid-cols-[minmax(260px,1fr)_minmax(320px,1.2fr)_minmax(300px,1fr)]">
                <section className="min-h-0 border-r border-border" aria-label={t("society.card.specs")}>
                  <AgentSpecSheet agent={agent} />
                </section>
                <section className="society-figure-column relative min-h-0" aria-label={t("society.card.figure")}>
                  <AgentFigureViewer recipe={agent.figure} />
                </section>
                <section
                  className="flex min-h-0 flex-col items-center justify-center gap-2 border-l border-border p-6 text-center"
                  aria-label={t("society.card.chat")}
                >
                  <MessageSquareDashed className="h-6 w-6 text-muted-foreground" aria-hidden />
                  <p className="text-sm font-medium text-foreground">{t("society.card.chat_empty_title")}</p>
                  <p className="max-w-[30ch] text-xs text-muted-foreground">{t("society.card.chat_empty_hint")}</p>
                </section>
              </div>
            </>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
