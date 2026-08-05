import { useEffect, useState } from "react";

import { useVoiceMode } from "@/hooks/useVoiceMode";
import { RealtimeTransportBroker } from "@/lib/realtimeTransportBroker";
import {
  REALTIME_TRANSPORT_PREPARE_EVENT,
  REALTIME_TRANSPORT_PREPARE_TTL_MS,
} from "@/lib/realtimeTransportIssue";
import { hasEmbeddedDesktopBridge } from "./BrowserRealtimeControl";

/** Authenticated, invisible WebRTC offer broker for native voice sessions. */
export function SubscriptionRealtimeTransportBroker() {
  const { mode, realtimeAvailable, requiresWebRtcOffer } = useVoiceMode();
  const [desktopCapabilityReady, setDesktopCapabilityReady] = useState(
    () => Boolean(window.__JARVIS_REALTIME_BROKER_TOKEN?.trim()),
  );
  // A pending "publish an offer now" request, plus the nonce that revives a
  // broker which already gave up. Both exist for the switch race: a provider
  // switch reconnects the LIVE call synchronously, while this client only
  // learns that the new transport needs an offer after a status round trip —
  // so the reopened session used to start with nothing registered.
  const [prepareUntil, setPrepareUntil] = useState(0);
  const [restartNonce, setRestartNonce] = useState(0);

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
    const onPrepare = () => {
      setPrepareUntil(Date.now() + REALTIME_TRANSPORT_PREPARE_TTL_MS);
      setRestartNonce((value) => value + 1);
    };
    window.addEventListener(REALTIME_TRANSPORT_PREPARE_EVENT, onPrepare);
    return () =>
      window.removeEventListener(REALTIME_TRANSPORT_PREPARE_EVENT, onPrepare);
  }, []);

  const preparing = prepareUntil > Date.now();

  useEffect(() => {
    if (
      mode !== "realtime" ||
      !realtimeAvailable ||
      // `preparing` covers the window in which this client cannot yet know
      // whether the transport it is switching TO needs an offer. One that does
      // not simply lets the registration expire — this never branches on a
      // provider name (AP-21).
      !(requiresWebRtcOffer || preparing) ||
      !hasEmbeddedDesktopBridge() ||
      !desktopCapabilityReady
    ) {
      return;
    }
    const broker = new RealtimeTransportBroker();
    broker.start();
    return () => broker.stop();
  }, [
    desktopCapabilityReady,
    mode,
    preparing,
    realtimeAvailable,
    requiresWebRtcOffer,
    restartNonce,
  ]);

  // Drop the prepare window once it elapses, so a broker kept alive only by it
  // is torn down instead of holding a peer connection open indefinitely.
  useEffect(() => {
    if (prepareUntil === 0) return;
    const remaining = prepareUntil - Date.now();
    if (remaining <= 0) {
      setPrepareUntil(0);
      return;
    }
    const timer = window.setTimeout(() => setPrepareUntil(0), remaining);
    return () => window.clearTimeout(timer);
  }, [prepareUntil]);

  return null;
}
