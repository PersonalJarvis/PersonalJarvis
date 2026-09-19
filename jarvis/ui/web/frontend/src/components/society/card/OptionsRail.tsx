/**
 * The chat-face right column: this agent's browser screen, its routines, and
 * a tucked-away Retire control. On the lead's card only, the typed chats and
 * the voice sessions join in, so a hung-up call is one click away. Extracted
 * from AgentCardOverlay so the overlay stays the layout and this file owns
 * what you can do to the agent.
 */
import { useEffect, useRef, useState } from "react";
import { MoreHorizontal } from "lucide-react";

import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";

import { encodeAgentPortrait } from "../agentPortrait";
import { useUpdateAgentPortrait, type SocietyAgent } from "../data";
import { AgentBrowserPreview } from "./AgentBrowserPreview";
import { AgentRoutinesList } from "./AgentRoutinesList";
import { JarvisHistoryRail } from "../chat/JarvisHistoryRail";
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
  const [routineOpen, setRoutineOpen] = useState(false);
  const [portraitBusy, setPortraitBusy] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);
  const portraitInputRef = useRef<HTMLInputElement>(null);
  const updatePortrait = useUpdateAgentPortrait();
  const pushToast = useEventStore((s) => s.pushToast);

  const savePortrait = async (portrait: string | null) => {
    setPortraitBusy(true);
    try {
      await updatePortrait(agent, portrait);
      pushToast("success", t(portrait ? "society.card.portrait_updated" : "society.card.portrait_removed"));
      setMore(false);
    } catch {
      pushToast("error", t("society.card.portrait_save_failed"));
    } finally {
      setPortraitBusy(false);
    }
  };

  const onPortraitChosen = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const portrait = await encodeAgentPortrait(file);
      await savePortrait(portrait);
    } catch (error) {
      const message = error instanceof Error ? error.message : "";
      const key = message === "portrait_too_large" ? "portrait_too_large" : "portrait_invalid_file";
      pushToast("error", t(`society.card.${key}`));
    }
  };

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
      <input
        ref={portraitInputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="hidden"
        aria-label={t("society.card.portrait_change")}
        onChange={(event) => { void onPortraitChosen(event); }}
      />
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
              {!sample ? (
                <>
                  <button
                    type="button"
                    role="menuitem"
                    disabled={portraitBusy}
                    className="block w-full rounded-md px-2 py-1.5 text-left text-sm text-foreground hover:bg-secondary disabled:opacity-50"
                    onClick={() => portraitInputRef.current?.click()}
                    data-testid="agent-portrait-change"
                  >
                    {t("society.card.portrait_change")}
                  </button>
                  {agent.figure?.portrait ? (
                    <button
                      type="button"
                      role="menuitem"
                      disabled={portraitBusy}
                      className="block w-full rounded-md px-2 py-1.5 text-left text-sm text-foreground hover:bg-secondary disabled:opacity-50"
                      onClick={() => { void savePortrait(null); }}
                      data-testid="agent-portrait-remove"
                    >
                      {t("society.card.portrait_remove")}
                    </button>
                  ) : null}
                </>
              ) : null}
              <RetireButton agent={agent} onRetired={onRetired} variant="rail" />
            </div>
          ) : null}
        </div>
      </header>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden px-3 pb-3 pt-2">
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
          className="min-h-0 flex-1"
        />
      </div>
    </aside>
  );
}

export default OptionsRail;
