/**
 * The chat-face right column: this agent's browser screen, its routines, and
 * a tucked-away Retire control. On the lead's card only, the typed chats and
 * the voice sessions join in, so a hung-up call is one click away. Extracted
 * from AgentCardOverlay so the overlay stays the layout and this file owns
 * what you can do to the agent.
 */
import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { MoreHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";

import { useUpdateAgentPortrait, type SocietyAgent } from "../data";
import { PortraitEditor } from "../PortraitEditor";
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
  const [uploadBusy, setUploadBusy] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [draftPortrait, setDraftPortrait] = useState<string | undefined>(agent.figure?.portrait);
  const moreRef = useRef<HTMLDivElement>(null);
  const updatePortrait = useUpdateAgentPortrait();
  const pushToast = useEventStore((s) => s.pushToast);

  const savePortrait = async (portrait: string): Promise<boolean> => {
    setPortraitBusy(true);
    try {
      await updatePortrait(agent, portrait);
      pushToast("success", t(portrait === "figure" ? "society.card.portrait_removed" : "society.card.portrait_updated"));
      setMore(false);
      return true;
    } catch {
      pushToast("error", t("society.card.portrait_save_failed"));
      return false;
    } finally {
      setPortraitBusy(false);
    }
  };

  useEffect(() => {
    setMore(false);
    setEditorOpen(false);
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
              {!sample ? (
                <>
                  <button
                    type="button"
                    role="menuitem"
                    disabled={portraitBusy}
                    className="block w-full rounded-md px-2 py-1.5 text-left text-sm text-foreground hover:bg-secondary disabled:opacity-50"
                    onClick={() => {
                      setDraftPortrait(agent.figure?.portrait);
                      setMore(false);
                      setEditorOpen(true);
                    }}
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
                      onClick={() => { void savePortrait("figure"); }}
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
      <Dialog.Root open={editorOpen} onOpenChange={setEditorOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[60] bg-scrim/70" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-[70] max-h-[90vh] w-[min(92vw,660px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border border-border bg-card p-5 shadow-float focus:outline-none">
            <Dialog.Title className="font-display text-base font-semibold text-foreground">
              {t("society.portrait.edit_title")}
            </Dialog.Title>
            <Dialog.Description className="mb-4 text-xs text-muted-foreground">
              {t("society.portrait.hint")}
            </Dialog.Description>
            {agent.figure ? (
              <PortraitEditor
                key={agent.agentId}
                figure={agent.figure}
                palette={agent.palette}
                name={agent.name}
                portrait={draftPortrait}
                onChange={setDraftPortrait}
                onBusyChange={setUploadBusy}
              />
            ) : null}
            <div className="mt-4 flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setEditorOpen(false)}>
                {t("society.create.cancel")}
              </Button>
              <Button type="button" disabled={portraitBusy || uploadBusy} data-testid="portrait-save"
                onClick={() => { void savePortrait(draftPortrait ?? "figure").then((saved) => { if (saved) setEditorOpen(false); }); }}>
                {t("society.portrait.save")}
              </Button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </aside>
  );
}

export default OptionsRail;
