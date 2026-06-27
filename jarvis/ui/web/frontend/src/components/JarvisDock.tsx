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

/** True for a native OS drag carrying files or draggable text/URL — the only
 *  thing the file-drop path acts on. `useMissionDrag` never fires for these. */
function hasNativePayload(dt: DataTransfer | null): boolean {
  if (!dt) return false;
  const types = Array.from(dt.types ?? []);
  return (
    types.includes("Files") ||
    types.includes("text/plain") ||
    types.includes("text/uri-list")
  );
}

/** Either an internal mission card or a native OS payload — anything the dock
 *  should bloom for and accept. */
function isDroppable(dt: DataTransfer | null): boolean {
  return hasMission(dt) || hasNativePayload(dt);
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
 * It mirrors the chosen on-screen display style: a slim bar for `jarvis_bar`,
 * the ghost mascot otherwise. This in-app surface is the cloud-first drop
 * target — it works in any browser, unlike the separate Tk overlay windows.
 */
export function JarvisDock() {
  const t = useT();
  const { config } = useOverlayStyle();
  const dragging = useMissionDrag((s) => s.dragging);
  const endDrag = useMissionDrag((s) => s.end);
  const [armed, setArmed] = useState(false); // a card is directly over the dock
  const [fileArmed, setFileArmed] = useState(false); // a native OS drag is in flight
  const [flash, setFlash] = useState(false); // brief post-drop "absorb" burst
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Mascot ONLY when explicitly selected; the slim bar for "jarvis_bar"/"none"
  // and while the style is still loading (config === null). Defaulting the
  // unknown/loading state to the bar (the documented default) stops a ghost
  // mascot from flashing in for a user who picked the bar.
  const isBar = config?.style !== "mascot";

  // Clear the pending "absorb" timer on unmount so it can't setState on a gone
  // component (the source of a stray act() warning and a small leak).
  useEffect(
    () => () => {
      if (flashTimer.current) clearTimeout(flashTimer.current);
    },
    [],
  );

  // A native OS drag that leaves the window (or ends anywhere) must disarm the
  // dock. The full-window catch layer has no dragleave of its own, so without
  // this the bloom could stick after the cursor exits the window over the catch
  // layer. Wired only while a file drag is armed.
  useEffect(() => {
    if (!fileArmed) return;
    const disarm = () => setFileArmed(false);
    const onWindowDragLeave = (e: DragEvent) => {
      // relatedTarget === null + a viewport-edge cursor = the drag left the
      // window entirely (not just moved between elements inside it).
      if (
        e.relatedTarget === null &&
        (e.clientX <= 0 ||
          e.clientY <= 0 ||
          e.clientX >= window.innerWidth ||
          e.clientY >= window.innerHeight)
      ) {
        setFileArmed(false);
      }
    };
    window.addEventListener("drop", disarm);
    window.addEventListener("dragend", disarm);
    window.addEventListener("dragleave", onWindowDragLeave);
    return () => {
      window.removeEventListener("drop", disarm);
      window.removeEventListener("dragend", disarm);
      window.removeEventListener("dragleave", onWindowDragLeave);
    };
  }, [fileArmed]);

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

  /** Ship a native OS drop (files or text/URL) to the proactive-reaction
   *  endpoint as multipart/form-data. Returns true when something was sent. */
  async function dropNative(dt: DataTransfer): Promise<boolean> {
    const files = Array.from(dt.files ?? []);
    const text =
      files.length === 0
        ? dt.getData("text/uri-list") || dt.getData("text/plain")
        : "";
    if (files.length === 0 && !text) return false;

    const form = new FormData();
    for (const file of files) form.append("files", file);
    if (text) form.set("text", text);
    form.set("surface", "dock");
    try {
      const threadId = await useEventStore.getState().ensureActiveThread();
      if (threadId) form.set("thread_id", threadId);
    } catch {
      // Tolerate a missing thread — the backend can still react.
    }
    try {
      // No Content-Type header: the browser sets the multipart boundary.
      const res = await fetch("/api/chat/drop", { method: "POST", body: form });
      return res.ok;
    } catch {
      return false;
    }
  }

  function celebrate() {
    playDropConfirm();
    setFlash(true);
    if (flashTimer.current) clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlash(false), 1100);
  }

  async function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setArmed(false);
    setFileArmed(false);
    const dt = e.dataTransfer;
    // Disambiguate: an internal mission card keeps the existing WS path; any
    // other native payload (files / text / URL) goes to the file-drop endpoint.
    const ok = hasMission(dt) ? await injectFrom(dt) : await dropNative(dt);
    endDrag(); // tear down the bloom whether or not the payload was valid
    if (!ok) return;
    celebrate();
  }

  function acceptOver(e: React.DragEvent) {
    if (isDroppable(e.dataTransfer)) {
      // preventDefault here is what turns the OS cursor from 🚫 into "copy".
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    }
  }

  function handleDragEnter(e: React.DragEvent) {
    if (hasMission(e.dataTransfer)) setArmed(true);
    else if (hasNativePayload(e.dataTransfer)) setFileArmed(true);
  }

  // A drag is "live" for the dock whenever an internal mission card is in
  // flight OR a native OS payload is hovering — the latter never reaches
  // `useMissionDrag`, so `fileArmed` is what blooms the dock for OS drops.
  const live = dragging || fileArmed;

  // One mutually-exclusive visual state — avoids conflicting Tailwind utilities
  // (scale-105 vs scale-110) fighting over which wins in the generated CSS.
  const state = flash
    ? "flash"
    : armed || fileArmed
      ? "armed"
      : live
        ? "bloom"
        : "idle";
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
    : armed || fileArmed || dragging
      ? t("jarvis_dock.drop_active")
      : null;

  // The dock earns screen space only when a drag is actually in flight (a
  // mission card via `dragging`, or a native OS payload via `fileArmed`) or has
  // just landed (`flash`, the brief absorb burst). In idle it stays mounted —
  // so the drop handler and catch layer remain wired — but is faded out and
  // non-interactive, so no permanent icon clutters the corner.
  const visible = live || flash;

  return (
    <>
      {live && (
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
        onDragEnter={handleDragEnter}
        onDragOver={acceptOver}
        onDragLeave={() => {
          setArmed(false);
          setFileArmed(false);
        }}
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
