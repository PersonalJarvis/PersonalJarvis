import { Suspense, lazy, useEffect, useState } from "react";

import { bootSettled } from "@/lib/bootStagger";
import { hasEmbeddedDesktopBridge } from "@/lib/embeddedDesktop";
import { onSharedReturnToWindow } from "@/lib/focusRefresh";
import {
  RESOLVED_HOLD_MS,
  cardEpisodes,
  isCardEligible,
  isMacClient,
  isPermissionOwnerWindow,
} from "@/lib/permissionPrompts";
import { useEventStore } from "@/store/events";
import { usePermissionsStore } from "@/store/permissions";

/**
 * Lazy on purpose. The card pulls in the button primitives, the copy builder and
 * the REST helpers, none of which anything needs until a person has something
 * to do about a permission; imported statically it would sit in the startup
 * chunk (bundle budget) for a card most sessions never see.
 */
const PermissionPromptLayer = lazy(() => import("./PermissionPromptLayer"));

/** A seed after a return to the window only happens after at least this long away. */
export const SEED_AFTER_HIDDEN_MS = 30_000;

/**
 * Re-read the embedded-bridge flag: the desktop shell injects it AFTER the page
 * loads (`window.__JARVIS_EMBEDDED_DESKTOP = true`, then it dispatches
 * `jarvis-token-ready`, see desktop_app.py) and pywebview announces its API with
 * `pywebviewready`. Both are events, so no fixed timer is needed: a slow start
 * still turns the window into the owner whenever the shell gets there.
 */
function useEmbeddedBridge(): boolean {
  const [embedded, setEmbedded] = useState(hasEmbeddedDesktopBridge);
  useEffect(() => {
    const check = () => setEmbedded(hasEmbeddedDesktopBridge());
    check();
    window.addEventListener("pywebviewready", check);
    window.addEventListener("jarvis-token-ready", check);
    return () => {
      window.removeEventListener("pywebviewready", check);
      window.removeEventListener("jarvis-token-ready", check);
    };
  }, []);
  return embedded;
}

/**
 * The always-mounted, tiny half of the permission prompt layer.
 *
 * It decides whether THIS window is the owner (the main window of an embedded
 * desktop app on macOS; see `isPermissionOwnerWindow`), seeds the episode list
 * from `GET /api/permissions/status` (mount, and after a long absence; the WS
 * welcome frame seeds too), and mounts the lazy card only while there is
 * something to show: an episode that needs the person, or the few seconds of
 * "Allowed" after one the card was showing. Nothing is fetched and nothing is
 * mounted in a detached window, a remote browser, or off macOS.
 */
export function PermissionPromptHost() {
  const solo = useEventStore((state) => state.solo);
  const embedded = useEmbeddedBridge();
  const owner = isPermissionOwnerWindow({
    solo,
    embedded,
    macClient: typeof navigator !== "undefined" && isMacClient(navigator.userAgent),
  });

  useEffect(() => {
    usePermissionsStore.getState().setOwner(owner);
    if (!owner) return undefined;
    let cancelled = false;
    // Non-critical: the first-mount burst must not spend a connection on it (bootStagger).
    void bootSettled().then(() => {
      if (!cancelled) void usePermissionsStore.getState().seed();
    });
    const stop = onSharedReturnToWindow(
      () => void usePermissionsStore.getState().seed({ activated: true }),
      { minHiddenMs: SEED_AFTER_HIDDEN_MS },
    );
    return () => {
      cancelled = true;
      stop();
    };
  }, [owner]);

  const headless = usePermissionsStore((state) => (state.snapshot ? state.snapshot.headless : null));
  const hasCard = usePermissionsStore(
    (state) =>
      cardEpisodes(state, new Set(Object.keys(state.inline))).length > 0,
  );
  const confirmedAt = usePermissionsStore(
    (state) => state.resolved.find((note) => note.granted && note.hadCard)?.ts ?? 0,
  );

  // Keep the layer mounted while its "Allowed" confirmation (and live region) is up.
  // Derived in the SAME render that drops the episode, never from an effect: a
  // render in between would unmount the layer and mount it again, and a polite
  // live region that is inserted already holding its text is not announced.
  const holdUntil = confirmedAt === 0 ? 0 : confirmedAt + RESOLVED_HOLD_MS;
  const holding = holdUntil > Date.now();
  const [, setTick] = useState(0);
  useEffect(() => {
    const wait = holdUntil - Date.now();
    if (holdUntil === 0 || wait <= 0) return undefined;
    // Re-render once the hold is over so the layer unmounts.
    const timer = window.setTimeout(() => setTick((value) => value + 1), wait + 50);
    return () => window.clearTimeout(timer);
  }, [holdUntil]);

  if (!isCardEligible({ owner, headless }) || !(hasCard || holding)) return null;
  return (
    <Suspense fallback={null}>
      <PermissionPromptLayer />
    </Suspense>
  );
}
