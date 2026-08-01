import { useEffect, useState } from "react";

import { useVoiceMode } from "@/hooks/useVoiceMode";
import { RealtimeTransportBroker } from "@/lib/realtimeTransportBroker";
import { hasEmbeddedDesktopBridge } from "./BrowserRealtimeControl";

/** Authenticated, invisible WebRTC offer broker for native voice sessions. */
export function SubscriptionRealtimeTransportBroker() {
  const { mode, realtimeAvailable, requiresWebRtcOffer } = useVoiceMode();
  const [desktopCapabilityReady, setDesktopCapabilityReady] = useState(
    () => Boolean(window.__JARVIS_REALTIME_BROKER_TOKEN?.trim()),
  );

  useEffect(() => {
    const refreshCapability = () => {
      setDesktopCapabilityReady(Boolean(window.__JARVIS_REALTIME_BROKER_TOKEN?.trim()));
    };
    // The native host normally injects the capability after React's first
    // render. Re-read it both now and at the host's explicit handoff event.
    refreshCapability();
    window.addEventListener("jarvis-token-ready", refreshCapability);
    return () => window.removeEventListener("jarvis-token-ready", refreshCapability);
  }, []);

  useEffect(() => {
    if (
      mode !== "realtime" ||
      !realtimeAvailable ||
      !requiresWebRtcOffer ||
      !hasEmbeddedDesktopBridge() ||
      !desktopCapabilityReady
    ) {
      return;
    }
    const broker = new RealtimeTransportBroker();
    broker.start();
    return () => broker.stop();
  }, [desktopCapabilityReady, mode, realtimeAvailable, requiresWebRtcOffer]);
  return null;
}
