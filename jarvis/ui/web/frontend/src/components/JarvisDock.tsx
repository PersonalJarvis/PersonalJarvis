import { useEffect, useRef, useState } from "react";
import { getWSClient } from "@/hooks/useWebSocket";
import { useOverlayStyle } from "@/hooks/useOverlayStyle";
import { useEventStore } from "@/store/events";
import { useMissionDrag } from "@/store/missionDrag";
import { MascotGigi } from "@/components/MascotGigi";
import { playDropConfirm } from "@/lib/sound";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/** MIME type carrying a mission reference from an Outputs card. Must match
 *  `MISSION_DND_MIME` in `views/OutputsView.tsx`. */
export const MISSION_DND_MIME = "application/x-jarvis-mission";

function hasMission(dt: DataTransfer | null): boolean {
  if (!dt) return false;
  return Array.from(dt.types ?? []).includes(MISSION_DND_MIME);
}

/**
 * A small, always-present "Jarvis presence" dock in the bottom-right corner.
 * Drop a mission/output card on it to pull that sub-agent task into the live
 * conversation — Jarvis speaks about it and it enters the context window.
 *
 * The feel is deliberately forgiving: the moment a mission card lifts anywhere
 * in the app (`useMissionDrag`), the dock blooms into a clear target and a soft
 * full-window catch layer accepts the drop everywhere — so the cursor never
 * shows the OS "no-drop" sign and a toss *near* Jarvis still lands. A successful
 * drop plays a quiet confirmation chime and an "absorb" burst.
 *
 * It mirrors the chosen on-screen display style: a slim bar for `whisper_bar`,
 * the ghost mascot otherwise. This in-app surface is the cloud-first drop
 * target — it works in any browser, unlike the separate Tk overlay windows.
 */
export function JarvisDock() {
  const t = useT();
  const { config } = useOverlayStyle();
  const dragging = useMissionDrag((s) => s.dragging);
  const endDrag = useMissionDrag((s) => s.end);
  const [armed, setArmed] = useState(false); // a card is directly over the dock
  const [flash, setFlash] = useState(false); // brief post-drop "absorb" burst
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isBar = config?.style === "whisper_bar";

  // Clear the pending "absorb" timer on unmount so it can't setState on a gone
  // component (the source of a stray act() warning and a small leak).
  useEffect(
    () => () => {
      if (flashTimer.current) clearTimeout(flashTimer.current);
    },
    [],
  );

  /** Parse + dispatch the dropped mission. Shared by the dock and the catch
   *  layer so a release anywhere near Jarvis behaves identically. */
  async function injectFrom(dt: DataTransfer): Promise<boolean> {
    const raw = dt.getData(MISSION_DND_MIME);
    if (!raw) return false;
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(raw);
    } catch {
      return false;
    }
    if (!payload || (!payload.utterance && !payload.slug)) return false;
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
    return true;
  }

  async function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setArmed(false);
    const ok = await injectFrom(e.dataTransfer);
    endDrag(); // tear down the bloom whether or not the payload was valid
    if (!ok) return;
    playDropConfirm();
    setFlash(true);
    if (flashTimer.current) clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlash(false), 1100);
  }

  function acceptOver(e: React.DragEvent) {
    if (hasMission(e.dataTransfer)) {
      // preventDefault here is what turns the OS cursor from 🚫 into "copy".
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    }
  }

  // One mutually-exclusive visual state — avoids conflicting Tailwind utilities
  // (scale-105 vs scale-110) fighting over which wins in the generated CSS.
  const state = flash ? "flash" : armed ? "armed" : dragging ? "bloom" : "idle";
  const stateClass = {
    idle: "border-border bg-card/70",
    bloom:
      "scale-105 border-primary/70 bg-primary/15 ring-2 ring-primary/40 shadow-[0_0_30px_rgba(255,214,10,0.28)] animate-[dock-breathe_2.2s_ease-in-out_infinite]",
    armed:
      "scale-110 border-primary bg-primary/25 ring-2 ring-primary shadow-[0_0_46px_rgba(255,214,10,0.5)]",
    flash:
      "scale-110 border-emerald-400 bg-emerald-400/15 ring-2 ring-emerald-400 shadow-[0_0_40px_rgba(52,211,153,0.5)]",
  }[state];

  const label = flash
    ? t("jarvis_dock.dropped")
    : armed || dragging
      ? t("jarvis_dock.drop_active")
      : null;

  // The dock earns screen space only when a mission card is actually in flight
  // (`dragging`) or has just landed (`flash`, the brief absorb burst). In idle
  // it stays mounted — so the drop handler and catch layer remain wired — but
  // is faded out and non-interactive, so no permanent icon clutters the corner.
  const visible = dragging || flash;

  return (
    <>
      {dragging && (
        <div
          data-testid="jarvis-dock-catch"
          aria-hidden
          onDragEnter={acceptOver}
          onDragOver={acceptOver}
          onDrop={handleDrop}
          className="jarvis-dock-catch fixed inset-0 z-40"
        />
      )}
      <div
        data-testid="jarvis-dock"
        role="button"
        aria-label={t("jarvis_dock.aria")}
        aria-hidden={visible ? undefined : true}
        title={t("jarvis_dock.hint")}
        onDragEnter={(e) => {
          if (hasMission(e.dataTransfer)) setArmed(true);
        }}
        onDragOver={acceptOver}
        onDragLeave={() => setArmed(false)}
        onDrop={handleDrop}
        className={cn(
          "fixed bottom-4 right-4 z-50 flex items-center gap-2 rounded-full border px-3 py-2 shadow-lg backdrop-blur transition-all duration-300 ease-out",
          visible ? "opacity-100" : "pointer-events-none opacity-0",
          stateClass,
        )}
      >
        {/* Expanding ring on a successful "absorb". */}
        {flash && (
          <span
            aria-hidden
            className="pointer-events-none absolute inset-0 rounded-full ring-2 ring-emerald-400/70 animate-[dock-ripple_0.9s_ease-out_forwards]"
          />
        )}

        <span className={cn(flash && "animate-[dock-pop_0.5s_ease-out]")}>
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
        </span>

        {label && (
          <span
            className={cn(
              "whitespace-nowrap text-xs font-medium",
              flash ? "text-emerald-300" : "text-primary",
            )}
          >
            {label}
          </span>
        )}
      </div>
    </>
  );
}
