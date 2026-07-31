import { useEffect } from "react";

import { useVoiceMode } from "@/hooks/useVoiceMode";
import { RealtimeTransportBroker } from "@/lib/realtimeTransportBroker";
import { hasEmbeddedDesktopBridge } from "./BrowserRealtimeControl";

/** Authenticated, invisible WebRTC offer broker for native voice sessions. */
export function SubscriptionRealtimeTransportBroker() {
  const { mode, realtimeAvailable, requiresWebRtcOffer } = useVoiceMode();

  useEffect(() => {
    if (
      mode !== "realtime" ||
      !realtimeAvailable ||
      !requiresWebRtcOffer ||
      !hasEmbeddedDesktopBridge() ||
      !window.__JARVIS_REALTIME_BROKER_TOKEN?.trim()
    ) {
      return;
    }
    const broker = new RealtimeTransportBroker();
    broker.start();
    return () => broker.stop();
  }, [mode, realtimeAvailable, requiresWebRtcOffer]);
  return null;
}
