import { useCallback, useEffect } from "react";

import { fetchVoiceRuntimeState } from "@/lib/voiceApi";
import { useDocumentVisible } from "@/hooks/useDocumentVisible";
import { useEventStore } from "@/store/events";

/**
 * Stop the voice surfaces from claiming a conversation that ended.
 *
 * The store's `voiceState` is an echo of `SystemStateChanged`, and that event
 * is one-shot: whatever the window heard last is what it keeps showing. Every
 * way of missing the closing transition therefore froze the bar mid-call — a
 * dropped socket, a backend restart (the fresh supervisor starts in IDLE and
 * publishes nothing, because for IT nothing changed), a window that mounted
 * after the fact. The result is a bar that says LISTENING with an End button
 * over a microphone nobody is holding (field report 2026-08-25), and it does
 * not heal on its own — the store has no other input.
 *
 * So the surfaces reconcile against the backend's own word (GET
 * /api/voice/state) at each of the three moments the gap can have opened:
 * the socket (re)connecting, the window coming back on screen, and a move to
 * another section — the last one being when a stale bar is actually looked at.
 *
 * Three rules keep this a correction and never a second source of truth:
 *
 *  * It only ever moves the store TO idle. Starting or advancing a call stays
 *    the events' job; a REST snapshot is always a little behind them.
 *  * An unreadable answer (offline, headless, an older backend without the
 *    field) changes nothing.
 *  * A state that changed while the request was in flight wins — a live event
 *    is newer than the snapshot that was already on its way.
 */
export function useVoiceStateResync(): void {
  const connected = useEventStore((s) => s.connected);
  const activeSection = useEventStore((s) => s.activeSection);
  const visible = useDocumentVisible();

  const reconcile = useCallback(async () => {
    const store = useEventStore.getState();
    const before = store.voiceState;
    // Idle is the state this hook corrects TO — nothing to check, and the
    // common case, so the section switch costs no request at all.
    if (before === "idle") return;

    const truth = await fetchVoiceRuntimeState();
    if (truth === null) return;
    if (truth.available && truth.voiceState !== "idle") return;

    const now = useEventStore.getState();
    if (now.voiceState !== before) return; // a live event overtook the snapshot
    now.setVoice("idle");
    // The live-transcript box follows the same session boundary the websocket
    // clears it on; left alone, the last utterance of the dead session sits
    // there looking like someone is still speaking.
    now.setTranscription("", true);
  }, []);

  useEffect(() => {
    if (!connected || !visible) return;
    void reconcile();
  }, [activeSection, connected, reconcile, visible]);
}
