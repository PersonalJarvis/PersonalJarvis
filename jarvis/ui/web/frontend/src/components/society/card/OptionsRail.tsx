/**
 * The chat-face right column: this agent's browser screen, its routines, and
 * a tucked-away Retire control. On the lead's card only, the typed chats and
 * the voice sessions join in, so a hung-up call is one click away. Extracted
 * from AgentCardOverlay so the overlay stays the layout and this file owns
 * what you can do to the agent.
 */
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { MoreHorizontal, Share2 } from "lucide-react";

import { useT } from "@/i18n";

import type { SocietyAgent } from "../data";
import { AgentBrowserPreview } from "./AgentBrowserPreview";
import { AgentRoutinesList } from "./AgentRoutinesList";
import { JarvisHistoryRail } from "../chat/JarvisHistoryRail";
import { RetireButton } from "./RetireButton";
const AgentAppearanceDialog = lazy(() => import("../companion/AgentAppearanceDialog").then(m => ({ default: m.AgentAppearanceDialog })));
const ShareAgentDialog = lazy(() => import("./ShareAgentDialog").then(m => ({ default: m.ShareAgentDialog })));

export interface OptionsRailProps {
  agent: SocietyAgent;
  onRetired: () => void;
  /** True when rows come from the sample roster rather than society.db. */
  sample?: boolean;
}

export function OptionsRail({ agent, onRetired, sample = false }: OptionsRailProps) {
  const t = useT();
  const [more, setMore] = useState(false);
  const [appearanceOpen, setAppearanceOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [routineOpen, setRoutineOpen] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMore(false);
    setAppearanceOpen(false);
    setShareOpen(false);
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
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-3 pb-3 pt-2">
        <div className="flex shrink-0 gap-2">
          <button type="button" onClick={() => setAppearanceOpen(true)} data-testid="edit-agent-appearance" className="min-w-0 flex-1 rounded-lg border border-border px-3 py-2 text-left text-sm font-medium text-foreground hover:bg-secondary">{t("society.companion.appearance")}</button>
          {/* The lead is every install's own Jarvis and sample rows are not real agents: neither is shareable. */}
          {agent.tier !== "lead" && !sample && (
            <button type="button" onClick={() => setShareOpen(true)} data-testid="share-agent" title={t("society.share.cta_hint")} className="flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm font-medium text-foreground hover:bg-secondary">
              <Share2 className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
              {t("society.share.cta")}
            </button>
          )}
        </div>
        <div className={routineOpen ? "hidden" : "contents"}>
          <AgentBrowserPreview agent={agent} />
          {agent.tier === "lead" ? <JarvisHistoryRail /> : null}
        </div>
        <AgentRoutinesList
          key={agent.agentId}
          agentId={agent.agentId}
          onDetailOpenChange={setRoutineOpen}
          sampleRoutines={sample ? agent.routines : undefined}
          variant="rail"
          className="min-h-48 flex-1"
        />
      </div>
      {appearanceOpen && <Suspense fallback={null}><AgentAppearanceDialog key={agent.agentId} agent={agent} sample={sample} onClose={() => setAppearanceOpen(false)} /></Suspense>}
      {shareOpen && <Suspense fallback={null}><ShareAgentDialog key={agent.agentId} agent={agent} onClose={() => setShareOpen(false)} /></Suspense>}
    </aside>
  );
}

export default OptionsRail;
