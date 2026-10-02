/**
 * Ctrl + `+` / `-` / `0` (or whatever chords are set under Settings →
 * Keyboard shortcuts) zoom the whole app window, the way they zoom a browser.
 *
 * ## Real window zoom, not CSS
 *
 * The level is handed to the desktop shell, which sets the WebView engine's own
 * page zoom (see `jarvis/ui/window_zoom.py`). CSS `zoom` on the page would be
 * the easy route and the wrong one: it scales `100vh` layouts past the window
 * edge and misplaces popovers. The engine zoom shrinks the CSS viewport
 * instead, so every layout simply reflows.
 *
 * ## Where it does nothing on purpose
 *
 * * A browser tab: the browser already zooms with these keys, persistently and
 *   per site. The keys are left to it.
 * * A desktop shell whose engine cannot zoom (the Qt backend): the shell says
 *   so on the first try, and the keys are left alone from then on, so the
 *   chord never looks swallowed.
 * * A focused terminal pane of the Agentic IDE: there Ctrl + `+` / `-` makes
 *   the terminal text bigger, the way every terminal does. That bridge sits on
 *   `window` in the capture phase and stops the event before it reaches this
 *   one, which is registered on `document` for exactly that ordering.
 * * While a shortcut recorder is capturing keys.
 */
import { useEffect } from "react";
import { create } from "zustand";

import { hasEmbeddedDesktopBridge } from "@/components/voice/BrowserRealtimeControl";
import { appZoomIntentFor, nextAppZoom } from "@/lib/appZoom";
import { armZoomTransition, installZoomTransition } from "@/lib/zoomTransition";
import { useAppZoomSettings } from "@/store/appZoomSettings";
import { useEventStore } from "@/store/events";

/** Whether this window's engine zooms: unknown until the first answer. */
export type AppZoomSupport = "unknown" | "native" | "browser" | "unsupported";

export const useAppZoomSupport = create<{ support: AppZoomSupport }>(() => ({
  support: "unknown",
}));

/**
 * The Chrome-style bubble that names the new level after a zoom step. `seq`
 * changes on every step so the bubble restarts its hide timer.
 */
export const useZoomIndicator = create<{ open: boolean; seq: number }>(() => ({
  open: false,
  seq: 0,
}));

export function showZoomIndicator(): void {
  useZoomIndicator.setState((s) => ({ open: true, seq: s.seq + 1 }));
}

export function hideZoomIndicator(): void {
  useZoomIndicator.setState({ open: false });
}

function windowView(): string | null {
  const { solo, activeSection } = useEventStore.getState();
  return solo ? activeSection : null;
}

/**
 * One request in flight at a time, and only the newest level waits behind it.
 * A held-down key repeats faster than the shell answers; firing a request per
 * repeat let them overtake each other, and the window visibly stepped back
 * and forth on its way to the level the user asked for.
 */
let inFlight = false;
let queued: number | null = null;
let applied: number | null = null;

/** Forget what this window applied — for tests, which share the module. */
export function resetAppZoomApplyState(): void {
  inFlight = false;
  queued = null;
  applied = null;
}

async function applyWindowZoom(level: number): Promise<void> {
  if (inFlight) {
    queued = level;
    return;
  }
  inFlight = true;
  try {
    await sendWindowZoom(level);
  } finally {
    inFlight = false;
    const next = queued;
    queued = null;
    if (next !== null && next !== applied) void applyWindowZoom(next);
  }
}

async function sendWindowZoom(level: number): Promise<void> {
  // The engine's resize is the cue for the glide (lib/zoomTransition); the
  // first apply at start-up has nothing to glide from.
  if (applied !== null && applied !== level) armZoomTransition();
  try {
    const res = await fetch("/api/window/zoom", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ factor: level, view: windowView() }),
    });
    const body = (res.ok ? await res.json() : null) as { ok?: boolean; reason?: string } | null;
    if (body?.ok) {
      applied = level;
      useAppZoomSupport.setState({ support: "native" });
      return;
    }
    // A failure on an engine that zoomed before is a hiccup, not a verdict.
    if (useAppZoomSupport.getState().support !== "native") {
      useAppZoomSupport.setState({ support: "unsupported" });
    }
    console.warn("Window zoom unavailable:", body?.reason ?? res.status);
  } catch (error: unknown) {
    console.warn("Window zoom failed", error);
  }
}

export function useAppZoom(): void {
  const enabled = useAppZoomSettings((s) => s.enabled);
  const bindings = useAppZoomSettings((s) => s.bindings);
  const level = useAppZoomSettings((s) => s.level);
  const embedded = hasEmbeddedDesktopBridge();

  useEffect(() => {
    if (!embedded) useAppZoomSupport.setState({ support: "browser" });
  }, [embedded]);

  useEffect(() => (embedded ? installZoomTransition(window) : undefined), [embedded]);

  // Apply the level to THIS window — on start (the window opens at 100 %), on
  // every step, and when another window changed it through storage.
  useEffect(() => {
    if (!embedded) return;
    void applyWindowZoom(level);
  }, [embedded, level]);

  useEffect(() => {
    if (!embedded || !enabled) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (useAppZoomSupport.getState().support === "unsupported") return;
      if (document.querySelector('[data-keybind-recording="true"]')) return;
      const intent = appZoomIntentFor(event, bindings);
      if (intent === null) return;
      // Also stops the WebView's own accelerator, which is on in debug builds.
      // Not stopped from propagating: the key tester on the Keyboard shortcuts
      // page listens further down and names what the chord did.
      event.preventDefault();
      const { level: current, setLevel } = useAppZoomSettings.getState();
      const next = nextAppZoom(current, intent);
      if (next !== current) setLevel(next);
      // Shown even at the ends of the range, like Chrome: the bubble is how
      // the user learns that 300 % is as far as it goes.
      showZoomIndicator();
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [embedded, enabled, bindings]);
}
