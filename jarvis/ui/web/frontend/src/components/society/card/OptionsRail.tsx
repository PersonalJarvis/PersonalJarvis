/**
 * The chat-face right column: this agent's browser screen, its routines, and
 * a tucked-away Retire control. Extracted from AgentCardOverlay so the overlay
 * stays the layout and this file owns what you can do to the agent.
 */
import { useEffect, useRef, useState } from "react";
import { MoreHorizontal } from "lucide-react";

import { useT } from "@/i18n";

import type { SocietyAgent } from "../data";
import { AgentBrowserPreview } from "./AgentBrowserPreview";
import { AgentRoutinesList } from "./AgentRoutinesList";
import { RetireButton } from "./RetireButton";

export interface OptionsRailProps {
  agent: SocietyAgent;
  onRetired: () => void;
  /** True when rows come from the sample roster rather than society.db. */
  sample?: boolean;
}

export function OptionsRail({ agent, onRetired, sample = false }: OptionsRailProps) {
  const t = useT();
  const [more, setMore] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMore(false);
  }, [agent.agentId]);

  useEffect(() => {
    if (!more) return;
    const onDown = (e: MouseEvent) => {
      if (!moreRef.current?.contains(e.target as Node)) setMore(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMore(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [more]);

  return (
    <aside
      className="flex h-full min-h-0 flex-col border-l border-border bg-sidebar"
      aria-label={t("society.card.options")}
      data-testid="agent-card-options"
    >
      <header className="flex shrink-0 items-center justify-between gap-2 px-3 pb-1 pt-3">
        <h2 className="font-display text-sm font-semibold tracking-tight text-foreground">
          {t("society.card.options")}
        </h2>
        <div ref={moreRef} className="relative">
          <button
            type="button"
            className="rounded-md p-1 text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong"
            aria-label={t("society.card.options_more")}
            aria-expanded={more}
            aria-haspopup="menu"
            onClick={() => setMore((v) => !v)}
            data-testid="agent-card-options-more"
          >
            <MoreHorizontal className="h-4 w-4" aria-hidden />
          </button>
          {more ? (
            <div
              role="menu"
              className="absolute right-0 top-full z-10 mt-1 w-56 rounded-md border border-border bg-popover p-3 shadow-float"
            >
              <RetireButton agent={agent} onRetired={onRetired} variant="rail" />
            </div>
          ) : null}
        </div>
      </header>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden px-3 pb-3 pt-2">
        <AgentBrowserPreview agent={agent} />
        <AgentRoutinesList
          agentId={agent.agentId}
          sampleRoutines={sample ? agent.routines : undefined}
          variant="rail"
          className="min-h-0 flex-1"
        />
      </div>
    </aside>
  );
}

export default OptionsRail;
