import { useState } from "react";
import { getWSClient } from "@/hooks/useWebSocket";
import { useOverlayStyle } from "@/hooks/useOverlayStyle";
import { useEventStore } from "@/store/events";
import { MascotGigi } from "@/components/MascotGigi";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/** MIME type carrying a mission reference from an Outputs card. Must match
 *  `MISSION_DND_MIME` in `views/OutputsView.tsx`. */
export const MISSION_DND_MIME = "application/x-jarvis-mission";

/**
 * A small, always-present "Jarvis presence" dock in the bottom-right corner.
 * Drop a mission/output card on it to pull that sub-agent task into the live
 * conversation — Jarvis speaks about it and it enters the context window.
 *
 * It mirrors the chosen on-screen display style: a slim bar for `whisper_bar`,
 * the ghost mascot otherwise. This in-app surface is the cloud-first drop
 * target — it works in any browser, unlike the separate Tk overlay windows.
 */
export function JarvisDock() {
  const t = useT();
  const { config } = useOverlayStyle();
  const [armed, setArmed] = useState(false); // a card is hovering
  const [flash, setFlash] = useState(false); // brief post-drop confirmation
  const isBar = config?.style === "whisper_bar";

  function hasMission(dt: DataTransfer | null): boolean {
    if (!dt) return false;
    return Array.from(dt.types ?? []).includes(MISSION_DND_MIME);
  }

  async function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setArmed(false);
    const raw = e.dataTransfer.getData(MISSION_DND_MIME);
    if (!raw) return;
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(raw);
    } catch {
      return;
    }
    if (!payload || (!payload.utterance && !payload.slug)) return;
    let threadId: string | undefined;
    try {
      threadId = await useEventStore.getState().ensureActiveThread();
    } catch {
      threadId = undefined;
    }
    getWSClient()?.send({
      type: "command",
      action: "mission.inject",
      payload: { ...payload, thread_id: threadId },
    });
    setFlash(true);
    setTimeout(() => setFlash(false), 1200);
  }

  return (
    <div
      data-testid="jarvis-dock"
      role="button"
      aria-label={t("jarvis_dock.aria")}
      title={t("jarvis_dock.hint")}
      onDragEnter={(e) => {
        if (hasMission(e.dataTransfer)) setArmed(true);
      }}
      onDragOver={(e) => {
        if (hasMission(e.dataTransfer)) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }
      }}
      onDragLeave={() => setArmed(false)}
      onDrop={onDrop}
      className={cn(
        "fixed bottom-4 right-4 z-50 flex items-center gap-2 rounded-full border px-3 py-2 shadow-lg backdrop-blur transition-all",
        armed
          ? "scale-110 border-primary bg-primary/20 ring-2 ring-primary"
          : "border-border bg-card/70",
        flash && "ring-2 ring-emerald-400",
      )}
    >
      {isBar ? (
        <span className="flex h-6 items-end gap-0.5" aria-hidden>
          <span className="h-3 w-1 rounded-sm bg-primary/80" />
          <span className="h-5 w-1 rounded-sm bg-primary" />
          <span className="h-2 w-1 rounded-sm bg-primary/60" />
          <span className="h-4 w-1 rounded-sm bg-primary/80" />
        </span>
      ) : (
        <MascotGigi size={28} reactToVoice enableComments={false} />
      )}
      {armed && (
        <span className="text-xs font-medium text-primary">
          {t("jarvis_dock.drop_here")}
        </span>
      )}
    </div>
  );
}
