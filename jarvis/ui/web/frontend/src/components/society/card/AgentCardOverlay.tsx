/**
 * The agent model card: a near-full-screen window above the world, the
 * world still visible through a dimmed, blurred scrim (MASTERPLAN §4.2).
 * Esc and ✕ close it.
 *
 * The card has TWO faces, and the chat is the one you land on (maintainer,
 * 2026-09-02). Read across a viewport cut into eight:
 *
 *   chat face     roster (1/8) | chat (6/8) | options (1/8)
 *   profile face  roster (1/8) | specs      | 3D figure
 *
 * The chat used to be a narrow third column beside the specs and the figure,
 * which made the thing you actually talk to the smallest pane on screen. Now
 * the chat owns the middle six eighths, the roster rail from the island moves
 * in on the left so you can switch agents without closing the card, and the
 * right eighth holds the agent's own controls. The first of them is Retire:
 * it archives the row AND has the island play the execution
 * (`world/retirement.ts`), which is why the card closes on its way out.
 *
 * Clicking the agent's identity in the header (or "Profile") turns the card
 * over: specs and the 3D figure, no chat, exactly the two panes the old card
 * had beside it. "Chat" turns it back.
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
import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { MessageSquare, Skull, User, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { AgentSwatch } from "../AgentSwatch";
import { findLead, useRetireAgent, type SocietyAgent } from "../data";
import { AgentChatPanel } from "../chat/AgentChatPanel";
import { AgentFigureViewer } from "../figures/AgentFigureViewer";
import { RosterRail } from "../roster/RosterRail";
import { AgentSpecSheet } from "./AgentSpecSheet";

/** Which face of the card is showing. */
type CardFace = "chat" | "profile";

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
  const open = agent !== null;
  const [face, setFace] = useState<CardFace>("chat");

  // Every opening starts on the chat. The face survives a switch between
  // agents inside the card — comparing two profiles is a real thing to do —
  // but never leaks from one opening of the card to the next.
  useEffect(() => {
    if (!open) setFace("chat");
  }, [open]);

  return (
    <Dialog.Root open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-scrim/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="agent-card"
          data-face={face}
          className="fixed inset-4 z-50 flex flex-col overflow-hidden rounded-lg border border-border bg-card shadow-float focus:outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none lg:inset-10"
        >
          {agent ? (
            <>
              <header className="flex shrink-0 items-center gap-3 border-b border-border px-5 py-3">
                <button
                  type="button"
                  onClick={() => setFace("profile")}
                  aria-label={t("society.card.open_profile")}
                  data-testid="agent-card-identity"
                  className={cn(
                    "-mx-2 flex min-w-0 flex-1 items-center gap-3 rounded-md px-2 py-1 text-left transition-colors",
                    "hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong",
                  )}
                >
                  <AgentSwatch agent={agent} size={40} />
                  {/* Title and description render as spans: a heading and a
                      paragraph are not valid inside a button, and Radix still
                      wires the dialog's labelling through `asChild`. */}
                  <span className="min-w-0 flex-1">
                    <Dialog.Title asChild>
                      <span className="block truncate font-display text-base font-semibold tracking-tight text-foreground">
                        {agent.name}
                      </span>
                    </Dialog.Title>
                    <Dialog.Description asChild>
                      <span className="block truncate text-xs text-muted-foreground">{agent.title}</span>
                    </Dialog.Description>
                  </span>
                </button>
                <Button
                  size="sm"
                  variant={face === "profile" ? "secondary" : "outline"}
                  className="gap-1.5"
                  onClick={() => setFace(face === "chat" ? "profile" : "chat")}
                  data-testid="agent-card-face-toggle"
                >
                  {face === "chat" ? (
                    <User className="h-3.5 w-3.5" aria-hidden />
                  ) : (
                    <MessageSquare className="h-3.5 w-3.5" aria-hidden />
                  )}
                  {face === "chat" ? t("society.card.profile") : t("society.card.chat")}
                </Button>
                <Badge variant="outline">{t(`society.state.${agent.state}`)}</Badge>
                <Dialog.Close
                  aria-label={t("society.card.close")}
                  className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong"
                >
                  <X className="h-4 w-4" aria-hidden />
                </Dialog.Close>
              </header>
              <div
                className={cn(
                  "grid min-h-0 flex-1",
                  face === "chat"
                    ? "grid-cols-[minmax(220px,1fr)_minmax(0,6fr)_minmax(190px,1fr)]"
                    : "grid-cols-[minmax(220px,1fr)_minmax(300px,3fr)_minmax(320px,4fr)]",
                )}
              >
                <RosterRail
                  agents={roster}
                  loading={rosterLoading}
                  sample={sample}
                  activeAgentId={agent.agentId}
                  onOpen={(id) => onSelectAgent?.(id)}
                  onCreate={() => onCreate?.()}
                  side="left"
                  className="w-full"
                />
                {face === "chat" ? (
                  <>
                    <section
                      className="flex min-h-0 flex-col"
                      aria-label={t("society.card.chat")}
                      data-testid="agent-card-chat"
                    >
                      <AgentChatPanel agent={agent} roster={roster} />
                    </section>
                    <OptionsRail agent={agent} roster={roster} onRetired={onClose} />
                  </>
                ) : (
                  <>
                    {/* No app border here: the sheet is a card of the world and
                        carries its own edge — its parchment against the figure's
                        lit lobby is the separation. */}
                    <section
                      className="min-h-0"
                      aria-label={t("society.card.specs")}
                      data-testid="agent-card-specs"
                    >
                      <AgentSpecSheet agent={agent} onOpenChat={() => setFace("chat")} />
                    </section>
                    <section
                      className="society-figure-column relative min-h-0"
                      aria-label={t("society.card.figure")}
                      data-testid="agent-card-figure"
                    >
                      <AgentFigureViewer recipe={agent.figure} />
                    </section>
                  </>
                )}
              </div>
            </>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/**
 * The right eighth: what you can DO to this agent, as opposed to what you can
 * say to it. Retirement is the only entry so far, and it sits at the bottom
 * away from everything else — it is not a control anyone should reach for by
 * accident.
 */
function OptionsRail({
  agent,
  roster,
  onRetired,
}: {
  agent: SocietyAgent;
  roster: SocietyAgent[];
  onRetired: () => void;
}) {
  const t = useT();
  return (
    <aside
      className="flex h-full min-h-0 flex-col border-l border-border bg-sidebar"
      aria-label={t("society.card.options")}
      data-testid="agent-card-options"
    >
      <div className="px-3 pt-3">
        <h2 className="font-display text-sm font-semibold tracking-tight text-foreground">
          {t("society.card.options")}
        </h2>
      </div>
      <p className="px-3 pt-2 text-xs text-muted-foreground">{t("society.card.options_hint")}</p>
      <div className="mt-auto p-3">
        <RetireControl agent={agent} roster={roster} onRetired={onRetired} />
      </div>
    </aside>
  );
}

/**
 * Retire this agent. Two presses, because it cannot be taken back: the first
 * arms it, the second archives the row and sends the lead across the island
 * to do it in person. The card closes so the ceremony is visible.
 *
 * The lead itself is never retirable — the roster refuses to archive it
 * (`roster.archive`), so the button says so rather than failing at the API.
 */
function RetireControl({
  agent,
  roster,
  onRetired,
}: {
  agent: SocietyAgent;
  roster: SocietyAgent[];
  onRetired: () => void;
}) {
  const t = useT();
  const retire = useRetireAgent();
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Arming never survives a switch to another agent in the rail.
  useEffect(() => {
    setArmed(false);
    setError(null);
  }, [agent.agentId]);

  if (agent.tier === "lead") {
    return <p className="text-xs text-muted-foreground">{t("society.card.retire_lead_blocked")}</p>;
  }

  const run = async () => {
    if (!armed) {
      setArmed(true);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await retire(agent, findLead(roster));
      onRetired();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setArmed(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2">
      <Button
        size="sm"
        variant={armed ? "destructive" : "outline"}
        className="w-full gap-1.5"
        disabled={busy}
        onClick={() => void run()}
        data-testid="agent-card-retire"
      >
        <Skull className="h-3.5 w-3.5" aria-hidden />
        {armed ? t("society.card.retire_confirm") : t("society.card.retire")}
      </Button>
      <p className="text-xs text-muted-foreground">
        {armed ? t("society.card.retire_armed_hint") : t("society.card.retire_hint")}
      </p>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
